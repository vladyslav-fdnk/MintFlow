from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

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


class SqlAlchemyExpenseRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, expense: Expense) -> None:
        self._session.add(_to_record(expense))
        self._session.commit()

    def get(self, *, expense_id: UUID, owner_id: UUID) -> Expense | None:
        record = self._session.scalar(
            select(ExpenseRecord).where(
                ExpenseRecord.id == expense_id,
                ExpenseRecord.owner_id == owner_id,
            )
        )
        return _to_domain(record) if record is not None else None

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
