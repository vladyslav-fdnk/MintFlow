from sqlalchemy import select
from sqlalchemy.orm import Session

from mintflow.domain.capture import Category
from mintflow.infrastructure.persistence.models import CategoryRecord


def _to_domain(record: CategoryRecord) -> Category:
    return Category(
        id=record.id,
        key=record.key,
        name=record.name,
        is_active=record.is_active,
    )


class SqlAlchemyCategoryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_key(self, key: str) -> Category | None:
        record = self._session.scalar(select(CategoryRecord).where(CategoryRecord.key == key))
        if record is None:
            return None
        return _to_domain(record)

    def list_active(self) -> list[Category]:
        records = self._session.scalars(
            select(CategoryRecord)
            .where(CategoryRecord.is_active.is_(True))
            .order_by(CategoryRecord.key)
        )
        return [_to_domain(record) for record in records]
