"""Every user-facing Telegram text (docs/telegram_client_design.md, T8).

English only for now; keeping the wording here makes localization a
translation task instead of a search through handlers.
"""

from typing import Final

LINKED: Final = (
    "Your Telegram account is now connected to MintFlow. "
    "Send /add to record an expense, or /help to see what I can do."
)
