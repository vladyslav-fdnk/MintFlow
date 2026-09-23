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
