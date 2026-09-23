from sqlalchemy.orm import Session

from mintflow.application.capture.expense_changes import ExpenseChangeRecord
from mintflow.infrastructure.persistence.models import ExpenseChangeRecordModel


class SqlAlchemyExpenseChangeRecordAppender:
    """Append-only: no update or delete, and no transaction of its own.

    ``append`` joins the caller's transaction and flushes so constraint
    violations surface immediately, but never commits: the change record and
    the Expense change it describes must commit or roll back together.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, record: ExpenseChangeRecord) -> None:
        self._session.add(
            ExpenseChangeRecordModel(
                id=record.id,
                expense_id=record.expense_id,
                actor_user_id=record.actor_user_id,
                occurred_at=record.occurred_at,
                change_type=record.change_type.value,
                changes=record.changes_document(),
            )
        )
        self._session.flush()
