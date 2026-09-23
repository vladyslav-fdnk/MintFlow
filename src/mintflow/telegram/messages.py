"""Every user-facing Telegram text (docs/telegram_client_design.md, T8).

English only for now; keeping the wording here makes localization a
translation task instead of a search through handlers. Texts never contain
account data for an unlinked user.
"""

from typing import Final

LINKED: Final = (
    "Your Telegram account is now connected to MintFlow. "
    "Send /add to record an expense, or /help to see what I can do."
)

WELCOME_UNLINKED: Final = (
    "Hi! MintFlow helps you record expenses here and understand them on the web.\n\n"
    "To start, connect this Telegram account: open MintFlow on the web, go to Settings, "
    "and choose Connect Telegram."
)

WELCOME_LINKED: Final = (
    "Welcome back! Send /add to record an expense, or /help to see what I can do."
)

LINK_CLAIMED: Final = (
    "Almost done. Go back to MintFlow on the web and confirm the connection there."
)

LINK_FAILED: Final = (
    "This connection link is invalid or has expired. "
    "Please start again from Settings in MintFlow on the web."
)

NOT_LINKED: Final = (
    "This Telegram account is not connected to MintFlow, so I can't record anything yet. "
    "Open MintFlow on the web, go to Settings, and choose Connect Telegram."
)

UNKNOWN_INPUT: Final = "I didn't understand that. Send /help to see what I can do."


def help_text(*, web_origin: str) -> str:
    return (
        "Here's what I can do:\n"
        "/add - record an expense\n"
        "/recent - show your latest expenses\n"
        "/cancel - cancel the expense you're recording\n"
        "/help - show this message\n\n"
        "Nothing is saved until you press Confirm on the review card.\n\n"
        f"See your history and spending summaries at {web_origin}"
    )


ASK_AMOUNT: Final = "How much did you spend? Send the amount, for example 12.50 or 12.50 EUR."
INVALID_AMOUNT: Final = (
    "That doesn't look like an amount. Send a number greater than zero, "
    "for example 12.50 or 12.50 EUR. Refunds and income aren't supported."
)
ASK_AMOUNT_WITH_CURRENCY: Final = (
    "Please include the currency, for example 12.50 EUR. "
    "You can set a default currency in MintFlow settings on the web."
)
ASK_CURRENCY: Final = "Which currency? Send a code such as EUR, USD, or GBP."
INVALID_CURRENCY: Final = "I don't know that currency. Send a code such as EUR, USD, or GBP."
ASK_MERCHANT: Final = "Where did you spend it? Send a shop name or a short description."
INVALID_MERCHANT: Final = "Please send a shop name or description of up to 140 characters."
ASK_CATEGORY: Final = "Choose a category:"
USE_CATEGORY_BUTTONS: Final = "Please choose a category with the buttons."
ASK_DATE: Final = "When was it? Choose below or send a date such as 2026-09-23 or 23.09.2026."
INVALID_DATE: Final = "That isn't a date I can read. Send it like 2026-09-23 or 23.09.2026."
FUTURE_DATE: Final = (
    "That date is too far in the future. Please send today's date or an earlier one."
)
NO_ACTIVE_DRAFT: Final = "There's no expense in progress. Send /add to record one."
DRAFT_NOT_ACTIVE: Final = "This expense is no longer active."
ALREADY_SAVED: Final = "This expense is already saved."
DRAFT_CANCELLED: Final = "Cancelled. Nothing was saved."
CONFIRM_FAILED: Final = (
    "I couldn't save this expense. Please check the date and amount, then try again."
)
DRAFT_IN_PROGRESS: Final = "You already have an expense in progress:"


def review_card(
    *,
    merchant: str | None,
    date: str,
    date_is_default: bool,
    amount: str,
    currency_is_default: bool,
    category: str,
) -> str:
    return (
        "Please review your expense:\n\n"
        f"Merchant: {merchant or 'Not specified'}\n"
        f"Date: {date}{' (default)' if date_is_default else ''}\n"
        f"Amount: {amount}{' (default currency)' if currency_is_default else ''}\n"
        f"Category: {category}\n\n"
        "Nothing is saved until you press Confirm."
    )


def saved(*, amount: str) -> str:
    return f"Saved: {amount}."


DRAFT_CONFLICT: Final = (
    "You already have an expense in progress. Continue it, or discard it and start a new one?"
)
NO_RECENT: Final = "You haven't saved any expenses yet. Send /add to record one."
RECENT_HEADER: Final = "Your latest expenses:"


def recent_line(*, date: str, merchant: str | None, amount: str, category: str) -> str:
    return f"{date} · {merchant or 'No merchant'} · {amount} · {category}"


RECEIPT_RECEIVED: Final = (
    "Got it! I'm reading your receipt. I'll show you what I found in a moment."
)
RECEIPT_STILL_READING: Final = "I'm still reading your receipt. I'll send the details shortly."
RECEIPT_ALBUM: Final = "Please send one receipt at a time, as a single photo."
RECEIPT_UNSUPPORTED: Final = (
    "I can only read receipt photos (JPEG, PNG, or WebP). Please send the receipt as a photo."
)
RECEIPT_TOO_LARGE: Final = "That file is too large. Please send a photo of the receipt instead."
RECEIPT_CONFLICT: Final = (
    "You already have an expense in progress. Continue it, or discard it and use this receipt?"
)
