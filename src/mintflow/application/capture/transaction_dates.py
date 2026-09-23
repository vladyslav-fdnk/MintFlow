from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from mintflow.domain.capture import TransactionDate
from mintflow.domain.user import Timezone

# A confirmed transaction date may be at most this far past the owner's local
# today, allowing for the owner's clock or timezone being slightly ahead.
FUTURE_TRANSACTION_DATE_TOLERANCE = timedelta(days=1)


def is_within_future_tolerance(
    transaction_date: TransactionDate, *, now: datetime, timezone: Timezone
) -> bool:
    """The single rule for how far ahead an Expense's transaction date may be.

    Shared by confirmation and post-confirmation editing so the two paths can
    never disagree about which dates are acceptable.
    """
    if now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    local_today = now.astimezone(ZoneInfo(timezone.value)).date()
    return transaction_date.value <= local_today + FUTURE_TRANSACTION_DATE_TOLERANCE
