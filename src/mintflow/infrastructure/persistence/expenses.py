from uuid import UUID

from sqlalchemy import Select, literal, select, tuple_, update
from sqlalchemy.orm import Session

from mintflow.application.capture.expense_history import (
    MAX_HISTORY_PAGE_SIZE,
    ExpenseHistoryFilter,
    ExpenseHistoryPage,
    ExpenseHistoryPosition,
)
from mintflow.domain.capture import (
    CaptureSource,
    CurrencyCode,
    Expense,
    MerchantName,
    Money,
    TransactionDate,
)
from mintflow.infrastructure.persistence.models import ExpenseRecord


def _to_record(expense: Expense) -> ExpenseRecord:
    return ExpenseRecord(
        id=expense.id,
        owner_id=expense.owner_id,
        amount_minor_units=expense.money.minor_units,
        amount_currency=expense.money.currency.value,
        transaction_date=expense.transaction_date.value,
        merchant_name=expense.merchant.value if expense.merchant is not None else None,
        category_key=expense.category_key,
        note=expense.note,
        source=expense.source.value,
        capture_draft_id=expense.capture_draft_id,
        receipt_id=expense.receipt_id,
        created_at=expense.created_at,
        modified_at=expense.modified_at,
        deleted_at=expense.deleted_at,
    )


def _record_values(expense: Expense) -> dict[str, object]:
    return {
        "amount_minor_units": expense.money.minor_units,
        "amount_currency": expense.money.currency.value,
        "transaction_date": expense.transaction_date.value,
        "merchant_name": expense.merchant.value if expense.merchant is not None else None,
        "category_key": expense.category_key,
        "note": expense.note,
        "modified_at": expense.modified_at,
        "deleted_at": expense.deleted_at,
    }


def _to_domain(record: ExpenseRecord) -> Expense:
    return Expense(
        id=record.id,
        owner_id=record.owner_id,
        money=Money(
            minor_units=record.amount_minor_units,
            currency=CurrencyCode(record.amount_currency),
        ),
        transaction_date=TransactionDate(record.transaction_date),
        merchant=(MerchantName(record.merchant_name) if record.merchant_name is not None else None),
        category_key=record.category_key,
        note=record.note,
        source=CaptureSource(record.source),
        capture_draft_id=record.capture_draft_id,
        receipt_id=record.receipt_id,
        created_at=record.created_at,
        modified_at=record.modified_at,
        deleted_at=record.deleted_at,
    )


def select_active_expenses(owner_id: UUID) -> Select[tuple[ExpenseRecord]]:
    """The single active-record rule for every financial-history read.

    Scopes to one owner and excludes soft-deleted Expenses (domain invariant
    26). History and analytics queries must start from this selection rather
    than re-deriving the rule.
    """
    return select(ExpenseRecord).where(
        ExpenseRecord.owner_id == owner_id,
        ExpenseRecord.deleted_at.is_(None),
    )


class SqlAlchemyExpenseRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, expense: Expense, *, commit: bool = True) -> None:
        """Persist a new Expense.

        Always flushes so the row is visible to the current transaction
        (for example to satisfy a foreign key from another table updated
        right after, within the same transaction) even when ``commit`` is
        False. ``commit=False`` lets a caller -- the confirmation use case
        -- compose this with ``SqlAlchemyCaptureDraftRepository.update``
        inside one atomic transaction; the caller is then responsible for
        the final commit.
        """
        self._session.add(_to_record(expense))
        self._session.flush()
        if commit:
            self._session.commit()

    def get(self, *, expense_id: UUID, owner_id: UUID) -> Expense | None:
        """Read an owned Expense in any deletion state.

        Not a financial-history read: used by confirmation idempotency, which
        must find the Expense a draft produced. History reads use
        ``get_active``.
        """
        record = self._session.scalar(
            select(ExpenseRecord).where(
                ExpenseRecord.id == expense_id,
                ExpenseRecord.owner_id == owner_id,
            )
        )
        return _to_domain(record) if record is not None else None

    def get_active(self, *, expense_id: UUID, owner_id: UUID) -> Expense | None:
        record = self._session.scalar(
            select_active_expenses(owner_id).where(ExpenseRecord.id == expense_id)
        )
        return _to_domain(record) if record is not None else None

    def list_history(
        self,
        *,
        owner_id: UUID,
        history_filter: ExpenseHistoryFilter,
        limit: int,
        after: ExpenseHistoryPosition | None = None,
    ) -> ExpenseHistoryPage:
        """Return one page of active Expenses, newest transaction date first.

        Keyset pagination on ``(transaction_date, created_at, id)``, all
        descending, so rows added or deleted between pages never shift the
        rows that existed throughout.
        """
        if not 1 <= limit <= MAX_HISTORY_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_HISTORY_PAGE_SIZE}")

        sort_key = tuple_(
            ExpenseRecord.transaction_date, ExpenseRecord.created_at, ExpenseRecord.id
        )
        statement = select_active_expenses(owner_id)
        if history_filter.date_from is not None:
            statement = statement.where(ExpenseRecord.transaction_date >= history_filter.date_from)
        if history_filter.date_to is not None:
            statement = statement.where(ExpenseRecord.transaction_date <= history_filter.date_to)
        if history_filter.category_keys:
            statement = statement.where(
                ExpenseRecord.category_key.in_(sorted(history_filter.category_keys))
            )
        if history_filter.currencies:
            statement = statement.where(
                ExpenseRecord.amount_currency.in_(
                    sorted(currency.value for currency in history_filter.currencies)
                )
            )
        if after is not None:
            statement = statement.where(
                sort_key
                < tuple_(
                    literal(after.transaction_date, ExpenseRecord.transaction_date.type),
                    literal(after.created_at, ExpenseRecord.created_at.type),
                    literal(after.expense_id, ExpenseRecord.id.type),
                )
            )
        statement = statement.order_by(
            ExpenseRecord.transaction_date.desc(),
            ExpenseRecord.created_at.desc(),
            ExpenseRecord.id.desc(),
        ).limit(limit + 1)

        records = self._session.scalars(statement).all()
        items = tuple(_to_domain(record) for record in records[:limit])
        next_position = ExpenseHistoryPosition.after(items[-1]) if len(records) > limit else None
        return ExpenseHistoryPage(items=items, next_position=next_position)

    def get_by_draft_id(self, *, capture_draft_id: UUID) -> Expense | None:
        record = self._session.scalar(
            select(ExpenseRecord).where(ExpenseRecord.capture_draft_id == capture_draft_id)
        )
        return _to_domain(record) if record is not None else None

    def update(self, expense: Expense) -> None:
        self._session.execute(
            update(ExpenseRecord)
            .where(ExpenseRecord.id == expense.id)
            .values(**_record_values(expense))
        )
        self._session.commit()
