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
from typing import Final, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from mintflow.application.capture import (
    CaptureDraftAccessDenied,
    CaptureDraftNotConfirmable,
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

_DRAFT_ACTION_PREFIX: Final = "d"
ADD_ACTION: Final = "add"


class DraftRepository(Protocol):
    def create(self, draft: CaptureDraft, *, commit: bool = True) -> None: ...

    def get(self, *, draft_id: UUID, owner_id: UUID) -> CaptureDraft | None: ...

    def update(self, draft: CaptureDraft, *, commit: bool = True) -> None: ...


class CategoryRepository(Protocol):
    def list_active(self) -> list[Category]: ...


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
        web_origin: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._conversations = conversations
        self._drafts = drafts
        self._categories = categories
        self._confirm = confirm
        self._web_origin = web_origin
        self._clock = clock

    # --- entry points ------------------------------------------------------------------------

    def on_command(self, user: User, chat_id: int, command: str) -> list[Outgoing] | None:
        """``/add`` and ``/cancel``; None for commands this flow does not own."""
        if command == "/add":
            return self._start(user, chat_id)
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
            self._review_reply(chat_id, conversation, draft),
        ]

    def on_callback(
        self, user: User, chat_id: int, message_id: int, callback_query_id: str, data: str
    ) -> list[Outgoing] | None:
        """Buttons; None when the data is not this flow's."""
        if data == ADD_ACTION:
            return [CallbackAnswer(callback_query_id), *self._start(user, chat_id)]
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

    # --- flow steps --------------------------------------------------------------------------

    def _start(self, user: User, chat_id: int) -> list[Outgoing]:
        conversation = self._lock(user)
        existing = self._active_draft(user, conversation)
        if existing is not None:
            return [
                Reply(chat_id, messages.DRAFT_IN_PROGRESS),
                *self._prompt_for(chat_id, conversation, existing),
            ]
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
        card = self._review_reply(chat_id, conversation, draft)
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
            CaptureDraftState.READY_FOR_REVIEW,
        ):
            # Confirmed elsewhere, cancelled, or expired: the conversation no longer points at it.
            self._conversations.save(conversation.finished(now=self._clock()))
            return None
        return draft

    def _prompt_for(
        self, chat_id: int, conversation: TelegramConversation, draft: CaptureDraft
    ) -> list[Outgoing]:
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
        return [self._review_reply(chat_id, conversation, draft)]

    def _review_reply(
        self, chat_id: int, conversation: TelegramConversation, draft: CaptureDraft
    ) -> Reply:
        names = {category.key: category.name for category in self._categories.list_active()}
        text = messages.review_card(
            merchant=draft.merchant.value if draft.merchant is not None else None,
            date=format_date(draft.transaction_date.value) if draft.transaction_date else "—",
            date_is_default=draft.transaction_date_source is DraftFieldSource.DEFAULT,
            amount=format_money(draft.amount) if draft.amount is not None else "—",
            currency_is_default=conversation.currency_is_default,
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
            (InlineButton("Add another", callback_data=ADD_ACTION),),
            (InlineButton("Open MintFlow", url=self._web_origin),),
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
