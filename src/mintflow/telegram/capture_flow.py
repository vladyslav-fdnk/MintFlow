"""Manual expense capture in Telegram (docs/telegram_client_design.md, T5-T7).

``/add`` starts a TELEGRAM_MANUAL draft with today's date (default) and asks
for the amount, then the merchant (skippable), then the category; the draft
then moves to review. The review card offers an edit for every field, Confirm,
and Cancel. Nothing is saved before ``ConfirmCaptureDraft`` commits.

Draft and conversation writes join the update's transaction (the handler
commits them with the update id). The user's conversation row is locked first,
so one user's updates are handled one at a time. Every button carries the
draft id, so a button on an old card can never act on a different draft.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from ipaddress import ip_address
from typing import Final, Protocol
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo

from mintflow.application.capture import (
    CaptureDraftAccessDenied,
    CaptureDraftNotConfirmable,
    ExpenseHistoryFilter,
    ExpenseHistoryPage,
    is_within_future_tolerance,
)
from mintflow.application.telegram import (
    AwaitingInput,
    TelegramConversation,
    TelegramConversationRepository,
)
from mintflow.domain.capture import (
    CaptureDraft,
    CaptureDraftState,
    CaptureSource,
    Category,
    CurrencyCode,
    DraftFieldSource,
    Expense,
    MerchantName,
    Receipt,
    TransactionDate,
)
from mintflow.domain.user import User
from mintflow.telegram import messages
from mintflow.telegram.bot_api import InlineButton, InlineKeyboard
from mintflow.telegram.outgoing import CallbackAnswer, EditMessage, Outgoing, Reply
from mintflow.telegram.parsing import (
    format_date,
    format_money,
    parse_amount,
    parse_currency,
    parse_date,
    to_money,
)
from mintflow.telegram.receipt_intake import ReceiptUpload

_DRAFT_ACTION_PREFIX: Final = "d"
ADD_ACTION: Final = "add"
RECENT_ACTION: Final = "recent"
RECENT_LIMIT: Final = 10
MANUAL_ACTION: Final = "manual"


class DraftRepository(Protocol):
    def create(self, draft: CaptureDraft, *, commit: bool = True) -> None: ...

    def get(self, *, draft_id: UUID, owner_id: UUID) -> CaptureDraft | None: ...

    def update(self, draft: CaptureDraft, *, commit: bool = True) -> None: ...


class ReceiptQueue(Protocol):
    def create(self, receipt: Receipt, *, telegram_file_id: str | None) -> None: ...


class CategoryRepository(Protocol):
    def list_active(self) -> list[Category]: ...


class ExpenseHistory(Protocol):
    def list_history(
        self, *, owner_id: UUID, history_filter: ExpenseHistoryFilter, limit: int
    ) -> ExpenseHistoryPage: ...


class DraftConfirmer(Protocol):
    def execute(self, *, draft_id: UUID, caller_id: UUID) -> Expense: ...


def draft_action(draft_id: UUID, action: str) -> str:
    """Callback data bound to one draft: ``d:<32 hex>:<action>`` (at most 64 bytes)."""
    return f"{_DRAFT_ACTION_PREFIX}:{draft_id.hex}:{action}"


def _parse_draft_action(data: str) -> tuple[UUID, str] | None:
    prefix, _, rest = data.partition(":")
    draft_hex, _, action = rest.partition(":")
    if prefix != _DRAFT_ACTION_PREFIX or not action:
        return None
    try:
        return UUID(hex=draft_hex), action
    except ValueError:
        return None


class ManualCaptureFlow:
    def __init__(
        self,
        *,
        conversations: TelegramConversationRepository,
        drafts: DraftRepository,
        categories: CategoryRepository,
        confirm: DraftConfirmer,
        history: ExpenseHistory,
        receipts: ReceiptQueue,
        web_origin: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._conversations = conversations
        self._drafts = drafts
        self._categories = categories
        self._confirm = confirm
        self._history = history
        self._receipts = receipts
        self._web_origin = web_origin
        self._clock = clock

    # --- entry points ------------------------------------------------------------------------

    def on_command(self, user: User, chat_id: int, command: str) -> list[Outgoing] | None:
        """``/add``, ``/cancel``, and ``/recent``; None for commands this flow does not own."""
        if command == "/add":
            return self._start(user, chat_id)
        if command == "/recent":
            return [self._recent(user, chat_id)]
        if command == "/cancel":
            conversation = self._lock(user)
            draft = self._active_draft(user, conversation)
            if draft is None:
                return [Reply(chat_id, messages.NO_ACTIVE_DRAFT)]
            return [Reply(chat_id, self._cancel(user, conversation, draft))]
        return None

    def on_text(self, user: User, chat_id: int, text: str) -> list[Outgoing]:
        conversation = self._lock(user)
        draft = self._active_draft(user, conversation)
        if draft is None:
            return [Reply(chat_id, messages.NO_ACTIVE_DRAFT)]
        if draft.state is CaptureDraftState.AWAITING_RECOGNITION:
            return [Reply(chat_id, messages.RECEIPT_STILL_READING)]
        awaiting = conversation.awaiting
        if awaiting is AwaitingInput.AMOUNT:
            return self._on_amount(user, chat_id, conversation, draft, text)
        if awaiting is AwaitingInput.CURRENCY:
            return self._on_currency(user, chat_id, conversation, draft, text)
        if awaiting is AwaitingInput.MERCHANT:
            return self._on_merchant(user, chat_id, conversation, draft, text)
        if awaiting is AwaitingInput.DATE:
            return self._on_date(user, chat_id, conversation, draft, text)
        if awaiting is AwaitingInput.CATEGORY:
            return [Reply(chat_id, messages.USE_CATEGORY_BUTTONS, self._category_keyboard(draft))]
        return [
            Reply(chat_id, messages.DRAFT_IN_PROGRESS),
            self.review_reply(chat_id, conversation, draft),
        ]

    def on_callback(
        self, user: User, chat_id: int, message_id: int, callback_query_id: str, data: str
    ) -> list[Outgoing] | None:
        """Buttons; None when the data is not this flow's."""
        if data == ADD_ACTION:
            return [CallbackAnswer(callback_query_id), *self._start(user, chat_id)]
        if data == RECENT_ACTION:
            return [CallbackAnswer(callback_query_id), self._recent(user, chat_id)]
        parsed = _parse_draft_action(data)
        if parsed is None:
            return None
        draft_id, action = parsed
        conversation = self._lock(user)
        draft = self._active_draft(user, conversation)
        if draft is None or draft.id != draft_id:
            stale = self._drafts.get(draft_id=draft_id, owner_id=user.id)
            answer = (
                messages.ALREADY_SAVED
                if stale is not None and stale.state is CaptureDraftState.CONFIRMED
                else messages.DRAFT_NOT_ACTIVE
            )
            return [CallbackAnswer(callback_query_id, answer)]
        replies = self._on_draft_action(user, chat_id, message_id, conversation, draft, action)
        return [CallbackAnswer(callback_query_id), *replies]

    def delay_notice(self, chat_id: int, draft: CaptureDraft) -> Reply:
        """Recognition is slow: offer to continue without it (design R7)."""
        keyboard: InlineKeyboard = (
            (
                InlineButton(
                    messages.RECEIPT_ENTER_MANUALLY,
                    callback_data=draft_action(draft.id, MANUAL_ACTION),
                ),
            ),
        )
        return Reply(chat_id, messages.RECEIPT_TAKING_LONGER, keyboard)

    def after_late_result(
        self,
        user: User,
        chat_id: int,
        conversation: TelegramConversation,
        before: CaptureDraft,
        draft: CaptureDraft,
    ) -> list[Outgoing]:
        """What to show after a result filled a draft the user already continued manually.

        ``draft`` is ``before`` with the result applied; the caller has saved it. The user
        sees the updated review card when they are looking at one, or when the receipt
        answered the amount question they were being asked. While they are answering any
        other question nothing is sent; their next answer leads to the updated values.
        """
        if (
            conversation.active_draft_id != draft.id
            or conversation.pending_receipt_file_id is not None
            or _fields(before) == _fields(draft)
        ):
            return []
        awaiting = conversation.awaiting
        if draft.state is CaptureDraftState.READY_FOR_REVIEW and awaiting is AwaitingInput.NOTHING:
            return [self.review_reply(chat_id, conversation, draft)]
        if (
            draft.state is CaptureDraftState.COLLECTING
            and awaiting is AwaitingInput.AMOUNT
            and draft.amount is not None
        ):
            return self._advance(
                user,
                chat_id,
                conversation,
                draft,
                next_when_collecting=AwaitingInput.MERCHANT,
                currency_is_default=False,
            )
        return []

    # --- flow steps --------------------------------------------------------------------------

    def _start(self, user: User, chat_id: int) -> list[Outgoing]:
        conversation = self._lock(user)
        existing = self._active_draft(user, conversation)
        if existing is not None:
            if conversation.pending_receipt_file_id is not None:
                self._conversations.save(conversation.with_pending_receipt(None, now=self._clock()))
            return [Reply(chat_id, messages.DRAFT_CONFLICT, _conflict_keyboard(existing))]
        return self._create_draft(user, chat_id, conversation)

    def on_media(self, user: User, chat_id: int, upload: ReceiptUpload) -> list[Outgoing]:
        """A receipt photo: queue it now, recognize it in the background (design R5)."""
        conversation = self._lock(user)
        existing = self._active_draft(user, conversation)
        if existing is not None:
            self._conversations.save(
                conversation.with_pending_receipt(upload.file_id, now=self._clock())
            )
            return [
                Reply(
                    chat_id,
                    messages.RECEIPT_CONFLICT,
                    _conflict_keyboard(existing, discard_label="Discard and use this receipt"),
                )
            ]
        return self._create_receipt_draft(user, chat_id, conversation, upload.file_id)

    def _create_receipt_draft(
        self, user: User, chat_id: int, conversation: TelegramConversation, file_id: str
    ) -> list[Outgoing]:
        now = self._clock()
        receipt = Receipt.receive(owner_id=user.id, now=now)
        self._receipts.create(receipt, telegram_file_id=file_id)
        today = now.astimezone(_zone(user)).date()
        draft = CaptureDraft.start_from_receipt(owner_id=user.id, receipt_id=receipt.id, now=now)
        draft = draft.set_transaction_date(
            caller_id=user.id,
            transaction_date=TransactionDate(today),
            now=now,
            source=DraftFieldSource.DEFAULT,
        )
        self._drafts.create(draft, commit=False)
        self._conversations.save(
            conversation.with_draft(
                draft.id, awaiting=AwaitingInput.NOTHING, currency_is_default=False, now=now
            )
        )
        return [Reply(chat_id, messages.RECEIPT_RECEIVED)]

    def _create_draft(
        self, user: User, chat_id: int, conversation: TelegramConversation
    ) -> list[Outgoing]:
        now = self._clock()
        today = now.astimezone(_zone(user)).date()
        draft = CaptureDraft.start(owner_id=user.id, source=CaptureSource.TELEGRAM_MANUAL, now=now)
        draft = draft.set_transaction_date(
            caller_id=user.id,
            transaction_date=TransactionDate(today),
            now=now,
            source=DraftFieldSource.DEFAULT,
        )
        self._drafts.create(draft, commit=False)
        self._conversations.save(
            conversation.with_draft(
                draft.id, awaiting=AwaitingInput.AMOUNT, currency_is_default=False, now=now
            )
        )
        return [Reply(chat_id, messages.ASK_AMOUNT)]

    def _recent(self, user: User, chat_id: int) -> Reply:
        """The latest confirmed, non-deleted Expenses; informational only (MVP section 5)."""
        page = self._history.list_history(
            owner_id=user.id, history_filter=ExpenseHistoryFilter(), limit=RECENT_LIMIT
        )
        keyboard: InlineKeyboard | None = self._open_web_rows() or None
        if not page.items:
            return Reply(chat_id, messages.NO_RECENT, keyboard)
        names = {category.key: category.name for category in self._categories.list_active()}
        lines = [
            messages.recent_line(
                date=format_date(expense.transaction_date.value),
                merchant=expense.merchant.value if expense.merchant is not None else None,
                amount=format_money(expense.money),
                category=names.get(expense.category_key, expense.category_key),
            )
            for expense in page.items
        ]
        return Reply(chat_id, "\n".join([messages.RECENT_HEADER, *lines]), keyboard)

    def _on_amount(
        self,
        user: User,
        chat_id: int,
        conversation: TelegramConversation,
        draft: CaptureDraft,
        text: str,
    ) -> list[Outgoing]:
        try:
            typed = parse_amount(text)
        except ValueError:
            return [Reply(chat_id, messages.INVALID_AMOUNT)]
        currency = typed.currency or user.default_currency
        if currency is None:
            return [Reply(chat_id, messages.ASK_AMOUNT_WITH_CURRENCY)]
        try:
            money = to_money(typed.value, currency)
        except ValueError:
            return [Reply(chat_id, messages.INVALID_AMOUNT)]
        now = self._clock()
        draft = draft.set_amount(caller_id=user.id, amount=money, now=now)
        return self._advance(
            user,
            chat_id,
            conversation,
            draft,
            next_when_collecting=AwaitingInput.MERCHANT,
            currency_is_default=typed.currency is None,
        )

    def _on_currency(
        self,
        user: User,
        chat_id: int,
        conversation: TelegramConversation,
        draft: CaptureDraft,
        text: str,
    ) -> list[Outgoing]:
        try:
            currency = parse_currency(text)
        except ValueError:
            return [Reply(chat_id, messages.INVALID_CURRENCY)]
        if draft.amount is None:
            return [Reply(chat_id, messages.ASK_AMOUNT)]
        # Re-express the same amount in the new currency's precision, e.g. 12.50 USD -> 12.50 EUR.
        value = _major_units(draft.amount.minor_units, draft.amount.currency)
        try:
            money = to_money(value, currency)
        except ValueError:
            self._conversations.save(
                conversation.waiting_for(AwaitingInput.AMOUNT, now=self._clock())
            )
            return [Reply(chat_id, messages.ASK_AMOUNT)]
        draft = draft.set_amount(caller_id=user.id, amount=money, now=self._clock())
        return self._advance(
            user,
            chat_id,
            conversation,
            draft,
            next_when_collecting=AwaitingInput.MERCHANT,
            currency_is_default=False,
        )

    def _on_merchant(
        self,
        user: User,
        chat_id: int,
        conversation: TelegramConversation,
        draft: CaptureDraft,
        text: str,
    ) -> list[Outgoing]:
        try:
            merchant = MerchantName(text)
        except ValueError:
            return [Reply(chat_id, messages.INVALID_MERCHANT)]
        draft = draft.set_merchant(caller_id=user.id, merchant=merchant, now=self._clock())
        return self._advance(
            user, chat_id, conversation, draft, next_when_collecting=AwaitingInput.CATEGORY
        )

    def _on_date(
        self,
        user: User,
        chat_id: int,
        conversation: TelegramConversation,
        draft: CaptureDraft,
        text: str,
    ) -> list[Outgoing]:
        try:
            transaction_date = parse_date(text)
        except ValueError:
            return [Reply(chat_id, messages.INVALID_DATE, _date_keyboard(draft))]
        return self._set_date(user, chat_id, conversation, draft, transaction_date)

    def _set_date(
        self,
        user: User,
        chat_id: int,
        conversation: TelegramConversation,
        draft: CaptureDraft,
        transaction_date: TransactionDate,
    ) -> list[Outgoing]:
        now = self._clock()
        if not is_within_future_tolerance(transaction_date, now=now, timezone=user.timezone):
            return [Reply(chat_id, messages.FUTURE_DATE, _date_keyboard(draft))]
        draft = draft.set_transaction_date(
            caller_id=user.id, transaction_date=transaction_date, now=now
        )
        return self._advance(
            user, chat_id, conversation, draft, next_when_collecting=AwaitingInput.MERCHANT
        )

    def _on_draft_action(
        self,
        user: User,
        chat_id: int,
        message_id: int,
        conversation: TelegramConversation,
        draft: CaptureDraft,
        action: str,
    ) -> list[Outgoing]:
        now = self._clock()
        if action == "skip" and conversation.awaiting is AwaitingInput.MERCHANT:
            return self._advance(
                user, chat_id, conversation, draft, next_when_collecting=AwaitingInput.CATEGORY
            )
        if action.startswith("cat:") and conversation.awaiting is AwaitingInput.CATEGORY:
            key = action.removeprefix("cat:")
            if key not in {category.key for category in self._categories.list_active()}:
                return [
                    Reply(chat_id, messages.USE_CATEGORY_BUTTONS, self._category_keyboard(draft))
                ]
            draft = draft.set_category_key(caller_id=user.id, category_key=key, now=now)
            if draft.state is CaptureDraftState.COLLECTING:
                draft = draft.mark_ready_for_review(caller_id=user.id, now=now)
            return self._show_review(chat_id, conversation, draft, message_id=message_id)
        if action.startswith("date:") and conversation.awaiting is AwaitingInput.DATE:
            today = now.astimezone(_zone(user)).date()
            offset = {"today": 0, "yesterday": 1}.get(action.removeprefix("date:"))
            if offset is None:
                return []
            return self._set_date(
                user, chat_id, conversation, draft, TransactionDate(today - timedelta(days=offset))
            )
        if action.startswith("edit:") and draft.state is CaptureDraftState.READY_FOR_REVIEW:
            field = action.removeprefix("edit:")
            awaiting = {
                "amount": AwaitingInput.AMOUNT,
                "currency": AwaitingInput.CURRENCY,
                "merchant": AwaitingInput.MERCHANT,
                "category": AwaitingInput.CATEGORY,
                "date": AwaitingInput.DATE,
            }.get(field)
            if awaiting is None:
                return []
            conversation = conversation.waiting_for(awaiting, now=now)
            self._conversations.save(conversation)
            return self._prompt_for(chat_id, conversation, draft)
        if action == "confirm" and draft.state is CaptureDraftState.READY_FOR_REVIEW:
            return self._confirm_draft(user, chat_id, message_id, conversation, draft)
        if action == "cancel":
            return [EditMessage(chat_id, message_id, self._cancel(user, conversation, draft))]
        if action == MANUAL_ACTION:
            if draft.state is CaptureDraftState.AWAITING_RECOGNITION:
                # Nothing was recognized yet, so the amount is the first missing field.
                draft = draft.continue_manually(caller_id=user.id, now=now)
                self._drafts.update(draft, commit=False)
                conversation = conversation.waiting_for(AwaitingInput.AMOUNT, now=now)
                self._conversations.save(conversation)
            return self._prompt_for(chat_id, conversation, draft)
        if action == "continue":
            if conversation.pending_receipt_file_id is not None:
                conversation = conversation.with_pending_receipt(None, now=now)
                self._conversations.save(conversation)
            return self._prompt_for(chat_id, conversation, draft)
        if action == "discard":
            # Cancel the old draft and start the new one in the same transaction.
            pending = conversation.pending_receipt_file_id
            self._cancel(user, conversation, draft)
            fresh = conversation.finished(now=now)
            if pending is not None:
                return self._create_receipt_draft(user, chat_id, fresh, pending)
            return self._create_draft(user, chat_id, fresh)
        return []

    def _advance(
        self,
        user: User,
        chat_id: int,
        conversation: TelegramConversation,
        draft: CaptureDraft,
        *,
        next_when_collecting: AwaitingInput,
        currency_is_default: bool | None = None,
    ) -> list[Outgoing]:
        """Save the edited draft, then ask for the next field or show the review card."""
        now = self._clock()
        if currency_is_default is not None:
            conversation = conversation.with_draft(
                draft.id,
                awaiting=conversation.awaiting,
                currency_is_default=currency_is_default,
                now=now,
            )
        if draft.state is CaptureDraftState.COLLECTING and draft.receipt_id is not None:
            # A receipt draft asks only for what recognition could not fill (design R6).
            if next_when_collecting is AwaitingInput.MERCHANT and draft.merchant is not None:
                next_when_collecting = AwaitingInput.CATEGORY
            if next_when_collecting is AwaitingInput.CATEGORY and draft.category_key is not None:
                draft = draft.mark_ready_for_review(caller_id=user.id, now=now)
        if draft.state is CaptureDraftState.COLLECTING:
            self._drafts.update(draft, commit=False)
            conversation = conversation.waiting_for(next_when_collecting, now=now)
            self._conversations.save(conversation)
            return self._prompt_for(chat_id, conversation, draft)
        return self._show_review(chat_id, conversation, draft, message_id=None)

    def _show_review(
        self,
        chat_id: int,
        conversation: TelegramConversation,
        draft: CaptureDraft,
        *,
        message_id: int | None,
    ) -> list[Outgoing]:
        conversation = conversation.waiting_for(AwaitingInput.NOTHING, now=self._clock())
        self._drafts.update(draft, commit=False)
        self._conversations.save(conversation)
        card = self.review_reply(chat_id, conversation, draft)
        if message_id is None:
            return [card]
        return [EditMessage(chat_id, message_id, card.text, card.keyboard)]

    def _confirm_draft(
        self,
        user: User,
        chat_id: int,
        message_id: int,
        conversation: TelegramConversation,
        draft: CaptureDraft,
    ) -> list[Outgoing]:
        try:
            expense = self._confirm.execute(draft_id=draft.id, caller_id=user.id)
        except CaptureDraftNotConfirmable:
            return [Reply(chat_id, messages.CONFIRM_FAILED)]
        except CaptureDraftAccessDenied:
            return [Reply(chat_id, messages.DRAFT_NOT_ACTIVE)]
        self._conversations.save(conversation.finished(now=self._clock()))
        return [
            EditMessage(
                chat_id,
                message_id,
                messages.saved(amount=format_money(expense.money)),
                self._after_save_keyboard(),
            )
        ]

    def _cancel(self, user: User, conversation: TelegramConversation, draft: CaptureDraft) -> str:
        now = self._clock()
        self._drafts.update(draft.cancel(caller_id=user.id, now=now), commit=False)
        self._conversations.save(conversation.finished(now=now))
        return messages.DRAFT_CANCELLED

    # --- helpers -----------------------------------------------------------------------------

    def _lock(self, user: User) -> TelegramConversation:
        return self._conversations.lock(user_id=user.id, now=self._clock())

    def _active_draft(self, user: User, conversation: TelegramConversation) -> CaptureDraft | None:
        if conversation.active_draft_id is None:
            return None
        draft = self._drafts.get(draft_id=conversation.active_draft_id, owner_id=user.id)
        if draft is None or draft.state not in (
            CaptureDraftState.COLLECTING,
            CaptureDraftState.AWAITING_RECOGNITION,
            CaptureDraftState.READY_FOR_REVIEW,
        ):
            # Confirmed elsewhere, cancelled, or expired: the conversation no longer points at it.
            self._conversations.save(conversation.finished(now=self._clock()))
            return None
        return draft

    def _prompt_for(
        self, chat_id: int, conversation: TelegramConversation, draft: CaptureDraft
    ) -> list[Outgoing]:
        if draft.state is CaptureDraftState.AWAITING_RECOGNITION:
            return [Reply(chat_id, messages.RECEIPT_STILL_READING)]
        awaiting = conversation.awaiting
        if awaiting is AwaitingInput.AMOUNT:
            return [Reply(chat_id, messages.ASK_AMOUNT)]
        if awaiting is AwaitingInput.CURRENCY:
            return [Reply(chat_id, messages.ASK_CURRENCY)]
        if awaiting is AwaitingInput.MERCHANT:
            keyboard = (
                ((InlineButton("Skip", callback_data=draft_action(draft.id, "skip")),),)
                if draft.state is CaptureDraftState.COLLECTING
                else None
            )
            return [Reply(chat_id, messages.ASK_MERCHANT, keyboard)]
        if awaiting is AwaitingInput.CATEGORY:
            return [Reply(chat_id, messages.ASK_CATEGORY, self._category_keyboard(draft))]
        if awaiting is AwaitingInput.DATE:
            return [Reply(chat_id, messages.ASK_DATE, _date_keyboard(draft))]
        return [self.review_reply(chat_id, conversation, draft)]

    def review_reply(
        self, chat_id: int, conversation: TelegramConversation, draft: CaptureDraft
    ) -> Reply:
        names = {category.key: category.name for category in self._categories.list_active()}
        from_receipt = DraftFieldSource.RECOGNITION
        amount_note = (
            messages.FROM_RECEIPT_NOTE
            if draft.amount_source is from_receipt
            else messages.DEFAULT_CURRENCY_NOTE
            if conversation.currency_is_default
            else None
        )
        date_note = (
            messages.FROM_RECEIPT_NOTE
            if draft.transaction_date_source is from_receipt
            else messages.DEFAULT_NOTE
            if draft.transaction_date_source is DraftFieldSource.DEFAULT
            else None
        )
        text = messages.review_card(
            merchant=draft.merchant.value if draft.merchant is not None else None,
            merchant_note=(
                messages.FROM_RECEIPT_NOTE if draft.merchant_source is from_receipt else None
            ),
            date=format_date(draft.transaction_date.value) if draft.transaction_date else "—",
            date_note=date_note,
            amount=format_money(draft.amount) if draft.amount is not None else "—",
            amount_note=amount_note,
            category=names.get(draft.category_key or "", draft.category_key or "—"),
        )

        def edit(label: str, field: str) -> InlineButton:
            return InlineButton(label, callback_data=draft_action(draft.id, f"edit:{field}"))

        keyboard: InlineKeyboard = (
            (edit("Edit amount", "amount"), edit("Edit currency", "currency")),
            (edit("Edit date", "date"), edit("Edit merchant", "merchant")),
            (edit("Edit category", "category"),),
            (
                InlineButton("Confirm", callback_data=draft_action(draft.id, "confirm")),
                InlineButton("Cancel", callback_data=draft_action(draft.id, "cancel")),
            ),
        )
        return Reply(chat_id, text, keyboard)

    def _category_keyboard(self, draft: CaptureDraft) -> InlineKeyboard:
        buttons = [
            InlineButton(category.name, callback_data=draft_action(draft.id, f"cat:{category.key}"))
            for category in self._categories.list_active()
        ]
        return _rows(buttons, per_row=2)

    def _after_save_keyboard(self) -> InlineKeyboard:
        return (
            (
                InlineButton("Add another", callback_data=ADD_ACTION),
                InlineButton("Recent", callback_data=RECENT_ACTION),
            ),
            *self._open_web_rows(),
        )

    def _open_web_rows(self) -> InlineKeyboard:
        """The "Open MintFlow" button, when Telegram will accept the Web origin as a link."""
        if not telegram_accepts_link(self._web_origin):
            return ()
        return ((InlineButton("Open MintFlow", url=self._web_origin),),)


def telegram_accepts_link(url: str) -> bool:
    """Whether Telegram accepts ``url`` in a button: a named host, not localhost or an IP.

    Telegram rejects the whole message (400) when one button has such a link, so a local
    development origin like ``http://localhost:8000`` must not become a button.
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme not in {"http", "https"} or host == "localhost" or "." not in host:
        return False
    try:
        ip_address(host)
    except ValueError:
        return True
    return False


def _fields(draft: CaptureDraft) -> tuple[object, ...]:
    return (draft.amount, draft.transaction_date, draft.merchant, draft.category_key)


def _conflict_keyboard(
    draft: CaptureDraft, *, discard_label: str = "Discard and start new"
) -> InlineKeyboard:
    return (
        (InlineButton("Continue", callback_data=draft_action(draft.id, "continue")),),
        (InlineButton(discard_label, callback_data=draft_action(draft.id, "discard")),),
    )


def _date_keyboard(draft: CaptureDraft) -> InlineKeyboard:
    return (
        (
            InlineButton("Today", callback_data=draft_action(draft.id, "date:today")),
            InlineButton("Yesterday", callback_data=draft_action(draft.id, "date:yesterday")),
        ),
    )


def _rows(buttons: Sequence[InlineButton], *, per_row: int) -> InlineKeyboard:
    return tuple(tuple(buttons[i : i + per_row]) for i in range(0, len(buttons), per_row))


def _major_units(minor_units: int, currency: CurrencyCode) -> Decimal:
    return Decimal(minor_units).scaleb(-currency.minor_unit_exponent)


def _zone(user: User) -> ZoneInfo:
    return ZoneInfo(user.timezone.value)
