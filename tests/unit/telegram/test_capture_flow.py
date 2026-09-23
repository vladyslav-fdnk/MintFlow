from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest

from mintflow.application.capture import (
    CaptureDraftNotConfirmable,
    ExpenseHistoryFilter,
    ExpenseHistoryPage,
)
from mintflow.application.telegram import AwaitingInput, TelegramConversation
from mintflow.domain.capture import (
    UNCATEGORIZED_KEY,
    CaptureDraft,
    CaptureDraftState,
    CaptureSource,
    Category,
    CurrencyCode,
    DraftFieldSource,
    Expense,
    Money,
    Receipt,
)
from mintflow.domain.user import Timezone, User
from mintflow.telegram import messages
from mintflow.telegram.capture_flow import (
    ADD_ACTION,
    RECENT_ACTION,
    ManualCaptureFlow,
    draft_action,
)
from mintflow.telegram.outgoing import CallbackAnswer, EditMessage, Outgoing, Reply
from mintflow.telegram.receipt_intake import ReceiptUpload

NOW = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)
CHAT = 4242
CARD = 900
EUR, USD, JPY = CurrencyCode("EUR"), CurrencyCode("USD"), CurrencyCode("JPY")


class FakeConversations:
    def __init__(self) -> None:
        self.rows: dict[UUID, TelegramConversation] = {}

    def lock(self, *, user_id: UUID, now: datetime) -> TelegramConversation:
        return self.rows.setdefault(user_id, TelegramConversation.idle(user_id=user_id, now=now))

    def save(self, conversation: TelegramConversation) -> None:
        self.rows[conversation.user_id] = conversation


class FakeDrafts:
    def __init__(self) -> None:
        self.rows: dict[UUID, CaptureDraft] = {}
        self.commits: list[bool] = []

    def create(self, draft: CaptureDraft, *, commit: bool = True) -> None:
        self.rows[draft.id] = draft
        self.commits.append(commit)

    def get(self, *, draft_id: UUID, owner_id: UUID) -> CaptureDraft | None:
        draft = self.rows.get(draft_id)
        return draft if draft is not None and draft.owner_id == owner_id else None

    def update(self, draft: CaptureDraft, *, commit: bool = True) -> None:
        self.rows[draft.id] = draft
        self.commits.append(commit)


class FakeCategories:
    def list_active(self) -> list[Category]:
        return [
            Category(id=uuid4(), key="groceries", name="Groceries", is_active=True),
            Category(id=uuid4(), key="transport", name="Transport", is_active=True),
            Category(id=uuid4(), key=UNCATEGORIZED_KEY, name="Uncategorized", is_active=True),
        ]


class FakeConfirm:
    """Idempotent like ConfirmCaptureDraft: one Expense per draft."""

    def __init__(self, drafts: FakeDrafts) -> None:
        self.drafts = drafts
        self.expenses: dict[UUID, Expense] = {}
        self.fail = False

    def execute(self, *, draft_id: UUID, caller_id: UUID) -> Expense:
        if self.fail:
            raise CaptureDraftNotConfirmable("date beyond tolerance")
        if draft_id in self.expenses:
            return self.expenses[draft_id]
        draft = self.drafts.rows[draft_id]
        assert draft.amount is not None and draft.transaction_date is not None
        expense = Expense.create(
            owner_id=caller_id,
            money=draft.amount,
            transaction_date=draft.transaction_date,
            category_key=draft.category_key or UNCATEGORIZED_KEY,
            capture_draft_id=draft.id,
            merchant=draft.merchant,
            source=draft.source,
            now=NOW,
        )
        self.expenses[draft_id] = expense
        self.drafts.rows[draft_id] = draft.confirm(
            caller_id=caller_id, expense_id=expense.id, now=NOW
        )
        return expense


class FakeReceipts:
    def __init__(self) -> None:
        self.created: list[tuple[Receipt, str | None]] = []

    def create(self, receipt: Receipt, *, telegram_file_id: str | None) -> None:
        self.created.append((receipt, telegram_file_id))


class FakeHistory:
    """Newest first, like the real query; only this user's confirmed Expenses."""

    def __init__(self, confirmer: FakeConfirm) -> None:
        self.confirmer = confirmer
        self.limits: list[int] = []

    def list_history(
        self, *, owner_id: UUID, history_filter: ExpenseHistoryFilter, limit: int
    ) -> ExpenseHistoryPage:
        self.limits.append(limit)
        items = sorted(
            (e for e in self.confirmer.expenses.values() if e.owner_id == owner_id and e.is_active),
            key=lambda e: (e.transaction_date.value, e.created_at, e.id),
            reverse=True,
        )
        return ExpenseHistoryPage(items=tuple(items[:limit]), next_position=None)


class Harness:
    def __init__(
        self, *, default_currency: CurrencyCode | None = EUR, timezone: str = "UTC"
    ) -> None:
        self.user = User.create(
            now=NOW, timezone=Timezone(timezone), default_currency=default_currency
        )
        self.conversations = FakeConversations()
        self.drafts = FakeDrafts()
        self.confirmer = FakeConfirm(self.drafts)
        self.history = FakeHistory(self.confirmer)
        self.receipts = FakeReceipts()
        self.flow = ManualCaptureFlow(
            conversations=self.conversations,
            drafts=self.drafts,
            categories=FakeCategories(),
            confirm=self.confirmer,
            history=self.history,
            receipts=self.receipts,
            web_origin="https://app.mintflow.test",
            clock=lambda: NOW,
        )

    # user actions
    def command(self, command: str) -> list[Outgoing]:
        result = self.flow.on_command(self.user, CHAT, command)
        assert result is not None
        return result

    def text(self, text: str) -> list[Outgoing]:
        return self.flow.on_text(self.user, CHAT, text)

    def press(self, action: str, *, draft_id: UUID | None = None) -> list[Outgoing]:
        data = (
            action
            if action in (ADD_ACTION, RECENT_ACTION)
            else draft_action(draft_id or self.draft.id, action)
        )
        result = self.flow.on_callback(self.user, CHAT, CARD, "cb", data)
        assert result is not None
        return result

    # state
    @property
    def conversation(self) -> TelegramConversation:
        return self.conversations.rows[self.user.id]

    @property
    def draft(self) -> CaptureDraft:
        draft_id = self.conversation.active_draft_id
        assert draft_id is not None
        return self.drafts.rows[draft_id]

    def to_review(self, amount: str = "12.50") -> None:
        self.command("/add")
        self.text(amount)
        self.text("Corner Shop")
        self.press("cat:groceries")


def _texts(outgoing: list[Outgoing]) -> list[str]:
    return [item.text for item in outgoing if isinstance(item, (Reply, EditMessage))]


def _last_card(outgoing: list[Outgoing]) -> Reply | EditMessage:
    cards = [item for item in outgoing if isinstance(item, (Reply, EditMessage))]
    return cards[-1]


def test_add_starts_a_telegram_draft_with_todays_date_as_default() -> None:
    harness = Harness(timezone="Asia/Tokyo")

    replies = harness.command("/add")

    assert _texts(replies) == [messages.ASK_AMOUNT]
    draft = harness.draft
    assert draft.source is CaptureSource.TELEGRAM_MANUAL
    assert draft.state is CaptureDraftState.COLLECTING
    # 09:00 UTC is 18:00 in Tokyo, still 23 Sep there.
    assert draft.transaction_date is not None and draft.transaction_date.value == date(2026, 9, 23)
    assert draft.transaction_date_source is DraftFieldSource.DEFAULT
    assert harness.conversation.awaiting is AwaitingInput.AMOUNT
    assert harness.drafts.commits == [False]  # joins the update's transaction


def test_happy_path_to_review_card_with_default_markers() -> None:
    harness = Harness()
    harness.command("/add")

    assert _texts(harness.text("12,50")) == [messages.ASK_MERCHANT]
    assert _texts(harness.text("Corner Shop")) == [messages.ASK_CATEGORY]
    review = harness.press("cat:groceries")

    assert isinstance(review[0], CallbackAnswer)
    card = _last_card(review)
    assert isinstance(card, EditMessage) and card.message_id == CARD
    assert "Merchant: Corner Shop" in card.text
    assert "Date: 23 Sep 2026 (default)" in card.text
    assert "Amount: 12.50 EUR (default currency)" in card.text
    assert "Category: Groceries" in card.text
    assert "Nothing is saved until you press Confirm." in card.text
    assert harness.draft.state is CaptureDraftState.READY_FOR_REVIEW
    assert harness.draft.amount == Money(minor_units=1250, currency=EUR)


def test_a_typed_currency_is_not_marked_default() -> None:
    harness = Harness()
    harness.to_review("12.50 USD")
    harness.press("edit:merchant")

    card = _last_card(harness.text("Kiosk"))

    assert harness.draft.amount == Money(minor_units=1250, currency=USD)
    assert harness.conversation.currency_is_default is False
    assert "Amount: 12.50 USD\n" in card.text


def test_without_a_default_currency_the_amount_must_include_one() -> None:
    harness = Harness(default_currency=None)
    harness.command("/add")

    assert _texts(harness.text("12.50")) == [messages.ASK_AMOUNT_WITH_CURRENCY]
    assert harness.conversation.awaiting is AwaitingInput.AMOUNT
    assert _texts(harness.text("12.50 GBP")) == [messages.ASK_MERCHANT]


@pytest.mark.parametrize("text", ["0", "-3", "abc", "12.505", "1500.5 JPY"])
def test_invalid_amounts_are_rejected_and_change_nothing(text: str) -> None:
    harness = Harness()
    harness.command("/add")

    assert _texts(harness.text(text)) == [messages.INVALID_AMOUNT]
    assert harness.draft.amount is None
    assert harness.conversation.awaiting is AwaitingInput.AMOUNT


def test_merchant_can_be_skipped_during_capture() -> None:
    harness = Harness()
    harness.command("/add")
    harness.text("5")

    replies = harness.press("skip")

    assert _texts(replies) == [messages.ASK_CATEGORY]
    assert harness.draft.merchant is None


def test_category_must_be_chosen_with_buttons() -> None:
    harness = Harness()
    harness.command("/add")
    harness.text("5")
    harness.press("skip")

    assert _texts(harness.text("groceries")) == [messages.USE_CATEGORY_BUTTONS]
    assert _texts(harness.press("cat:not_a_category")) == [messages.USE_CATEGORY_BUTTONS]
    assert harness.draft.category_key is None


def test_every_field_can_be_edited_from_the_review_card() -> None:
    harness = Harness()
    harness.to_review()

    assert _texts(harness.press("edit:amount")) == [messages.ASK_AMOUNT]
    assert "Amount: 20.00 EUR" in _last_card(harness.text("20")).text

    harness.press("edit:currency")
    assert "Amount: 20.00 USD\n" in _last_card(harness.text("usd")).text

    harness.press("edit:merchant")
    assert "Merchant: Bakery" in _last_card(harness.text("Bakery")).text

    harness.press("edit:date")
    card = _last_card(harness.press("date:yesterday"))
    assert "Date: 22 Sep 2026\n" in card.text

    harness.press("edit:date")
    assert "Date: 01 Sep 2026\n" in _last_card(harness.text("01.09.2026")).text

    harness.press("edit:category")
    assert "Category: Transport" in _last_card(harness.press("cat:transport")).text
    assert harness.draft.state is CaptureDraftState.READY_FOR_REVIEW


def test_changing_to_a_currency_with_fewer_decimals_asks_for_the_amount_again() -> None:
    harness = Harness()
    harness.to_review("12.50")
    harness.press("edit:currency")

    assert _texts(harness.text("JPY")) == [messages.ASK_AMOUNT]
    assert harness.draft.amount == Money(minor_units=1250, currency=EUR)
    assert "Amount: 1,500 JPY" in _last_card(harness.text("1500 JPY")).text


def test_dates_too_far_in_the_future_are_rejected() -> None:
    harness = Harness()
    harness.to_review()
    harness.press("edit:date")

    assert _texts(harness.text("2026-09-25")) == [messages.FUTURE_DATE]
    assert _texts(harness.text("31.02.2026")) == [messages.INVALID_DATE]
    assert harness.draft.transaction_date is not None
    assert harness.draft.transaction_date.value == date(2026, 9, 23)


def test_confirm_saves_once_and_reports_the_amount() -> None:
    harness = Harness()
    harness.to_review()
    draft_id = harness.draft.id

    saved = harness.press("confirm")
    again = harness.press("confirm", draft_id=draft_id)

    card = _last_card(saved)
    assert isinstance(card, EditMessage)
    assert card.text == messages.saved(amount="12.50 EUR")
    assert len(harness.confirmer.expenses) == 1
    assert again == [CallbackAnswer("cb", messages.ALREADY_SAVED)]
    assert harness.conversation.active_draft_id is None


def test_confirm_is_only_offered_from_review() -> None:
    harness = Harness()
    harness.command("/add")

    assert harness.press("confirm") == [CallbackAnswer("cb")]
    assert harness.confirmer.expenses == {}


def test_a_rejected_confirmation_keeps_the_draft_for_correction() -> None:
    harness = Harness()
    harness.to_review()
    harness.confirmer.fail = True

    replies = harness.press("confirm")

    assert _texts(replies) == [messages.CONFIRM_FAILED]
    assert harness.draft.state is CaptureDraftState.READY_FOR_REVIEW


def test_cancel_from_the_card_and_with_the_command() -> None:
    harness = Harness()
    harness.to_review()
    first = harness.draft.id

    assert _texts(harness.press("cancel")) == [messages.DRAFT_CANCELLED]
    assert harness.drafts.rows[first].state is CaptureDraftState.CANCELLED
    assert _texts(harness.command("/cancel")) == [messages.NO_ACTIVE_DRAFT]

    harness.command("/add")
    second = harness.draft.id
    assert _texts(harness.command("/cancel")) == [messages.DRAFT_CANCELLED]
    assert harness.drafts.rows[second].state is CaptureDraftState.CANCELLED


def test_buttons_on_old_or_foreign_cards_change_nothing() -> None:
    harness = Harness()
    harness.to_review()
    harness.press("cancel")
    cancelled = harness.drafts.rows[next(iter(harness.drafts.rows))]
    harness.command("/add")

    stale = harness.press("confirm", draft_id=cancelled.id)
    foreign = harness.press("confirm", draft_id=uuid4())

    assert stale == [CallbackAnswer("cb", messages.DRAFT_NOT_ACTIVE)]
    assert foreign == [CallbackAnswer("cb", messages.DRAFT_NOT_ACTIVE)]
    assert harness.confirmer.expenses == {}


def test_text_without_a_draft_points_to_add() -> None:
    harness = Harness()

    assert _texts(harness.text("12.50")) == [messages.NO_ACTIVE_DRAFT]


def test_add_while_a_draft_is_active_offers_continue_or_discard() -> None:
    harness = Harness()
    harness.command("/add")
    first = harness.draft.id

    replies = harness.command("/add")

    [reply] = replies
    assert isinstance(reply, Reply) and reply.text == messages.DRAFT_CONFLICT
    assert reply.keyboard is not None
    labels = [button.text for row in reply.keyboard for button in row]
    assert labels == ["Continue", "Discard and start new"]
    assert harness.draft.id == first
    assert len(harness.drafts.rows) == 1


def test_continue_repeats_the_current_step() -> None:
    harness = Harness()
    harness.command("/add")
    harness.text("5")
    harness.command("/add")

    assert _texts(harness.press("continue")) == [messages.ASK_MERCHANT]


def test_discard_cancels_the_old_draft_and_starts_a_new_one() -> None:
    harness = Harness()
    harness.to_review()
    old = harness.draft.id
    harness.command("/add")

    replies = harness.press("discard")

    assert _texts(replies) == [messages.ASK_AMOUNT]
    assert harness.drafts.rows[old].state is CaptureDraftState.CANCELLED
    assert harness.draft.id != old
    assert harness.draft.state is CaptureDraftState.COLLECTING
    assert harness.conversation.awaiting is AwaitingInput.AMOUNT
    assert all(commit is False for commit in harness.drafts.commits)


def test_add_another_button_also_respects_an_active_draft() -> None:
    harness = Harness()
    harness.command("/add")

    replies = harness.press(ADD_ACTION)

    assert _texts(replies) == [messages.DRAFT_CONFLICT]


def _save(harness: Harness, amount: str, merchant: str | None, category: str) -> None:
    harness.command("/add")
    harness.text(amount)
    if merchant is None:
        harness.press("skip")
    else:
        harness.text(merchant)
    harness.press(f"cat:{category}")
    harness.press("confirm")


def test_recent_lists_the_latest_ten_saved_expenses() -> None:
    harness = Harness()
    for index in range(12):
        _save(harness, f"{index + 1}", f"Shop {index}", "groceries")

    [reply] = harness.command("/recent")

    assert isinstance(reply, Reply)
    lines = reply.text.split("\n")
    assert lines[0] == messages.RECENT_HEADER
    assert len(lines) == 11
    assert harness.history.limits == [10]
    assert reply.keyboard is not None
    assert reply.keyboard[0][0].url == "https://app.mintflow.test"


def test_recent_line_shows_date_merchant_amount_currency_and_category() -> None:
    harness = Harness()
    _save(harness, "1500 JPY", None, "transport")

    [reply] = harness.command("/recent")

    assert isinstance(reply, Reply)
    assert reply.text.split("\n")[1] == "23 Sep 2026 · No merchant · 1,500 JPY · Transport"


def test_recent_with_no_expenses_points_to_add() -> None:
    harness = Harness()

    [reply] = harness.command("/recent")

    assert isinstance(reply, Reply) and reply.text == messages.NO_RECENT


def test_recent_button_after_saving() -> None:
    harness = Harness()
    _save(harness, "3", "Kiosk", "groceries")

    replies = harness.press(RECENT_ACTION)

    assert replies[0] == CallbackAnswer("cb")
    assert "Kiosk" in _texts(replies)[0]


def test_add_another_button_starts_a_new_draft_after_saving() -> None:
    harness = Harness()
    harness.to_review()
    harness.press("confirm")

    replies = harness.press(ADD_ACTION)

    assert replies[0] == CallbackAnswer("cb")
    assert _texts(replies) == [messages.ASK_AMOUNT]
    assert len(harness.drafts.rows) == 2


def test_callback_data_fits_telegrams_limit() -> None:
    longest = draft_action(uuid4(), f"cat:{'x' * 20}")

    assert len(longest.encode()) <= 64


def test_unknown_commands_are_not_this_flows() -> None:
    harness = Harness()

    assert harness.flow.on_command(harness.user, CHAT, "/settings") is None
    assert harness.flow.on_callback(harness.user, CHAT, CARD, "cb", "other") is None


def _send_photo(harness: Harness, file_id: str = "photo-1") -> list[Outgoing]:
    return harness.flow.on_media(harness.user, CHAT, ReceiptUpload(file_id=file_id))


def test_a_receipt_photo_queues_a_receipt_and_a_waiting_draft() -> None:
    harness = Harness()

    replies = _send_photo(harness)

    assert _texts(replies) == [messages.RECEIPT_RECEIVED]
    [(receipt, file_id)] = harness.receipts.created
    assert file_id == "photo-1"
    draft = harness.draft
    assert draft.state is CaptureDraftState.AWAITING_RECOGNITION
    assert draft.source is CaptureSource.TELEGRAM_RECEIPT
    assert draft.receipt_id == receipt.id
    assert draft.transaction_date_source is DraftFieldSource.DEFAULT
    assert harness.drafts.commits == [False]


def test_text_while_reading_a_receipt_gets_a_wait_message() -> None:
    harness = Harness()
    _send_photo(harness)

    assert _texts(harness.text("12.50")) == [messages.RECEIPT_STILL_READING]
    assert harness.draft.amount is None


def test_a_waiting_receipt_draft_can_be_cancelled() -> None:
    harness = Harness()
    _send_photo(harness)
    draft_id = harness.draft.id

    assert _texts(harness.command("/cancel")) == [messages.DRAFT_CANCELLED]
    assert harness.drafts.rows[draft_id].state is CaptureDraftState.CANCELLED


def test_a_photo_during_another_draft_is_held_until_the_user_chooses() -> None:
    harness = Harness()
    harness.command("/add")
    manual = harness.draft.id

    replies = _send_photo(harness, "held-photo")

    [reply] = replies
    assert isinstance(reply, Reply) and reply.text == messages.RECEIPT_CONFLICT
    assert reply.keyboard is not None
    assert [button.text for row in reply.keyboard for button in row] == [
        "Continue",
        "Discard and use this receipt",
    ]
    assert harness.conversation.pending_receipt_file_id == "held-photo"
    assert harness.receipts.created == []
    assert harness.draft.id == manual


def test_discard_uses_the_held_photo() -> None:
    harness = Harness()
    harness.command("/add")
    manual = harness.draft.id
    _send_photo(harness, "held-photo")

    replies = harness.press("discard", draft_id=manual)

    assert _texts(replies) == [messages.RECEIPT_RECEIVED]
    assert harness.drafts.rows[manual].state is CaptureDraftState.CANCELLED
    [(_receipt, file_id)] = harness.receipts.created
    assert file_id == "held-photo"
    assert harness.draft.state is CaptureDraftState.AWAITING_RECOGNITION
    assert harness.conversation.pending_receipt_file_id is None


def test_continue_forgets_the_held_photo() -> None:
    harness = Harness()
    harness.command("/add")
    _send_photo(harness, "held-photo")

    assert _texts(harness.press("continue")) == [messages.ASK_AMOUNT]
    assert harness.conversation.pending_receipt_file_id is None
    assert harness.receipts.created == []


def test_add_after_a_held_photo_discards_to_a_manual_draft() -> None:
    harness = Harness()
    harness.command("/add")
    manual = harness.draft.id
    _send_photo(harness, "held-photo")
    harness.command("/add")

    replies = harness.press("discard", draft_id=manual)

    assert _texts(replies) == [messages.ASK_AMOUNT]
    assert harness.receipts.created == []
    assert harness.draft.source is CaptureSource.TELEGRAM_MANUAL
