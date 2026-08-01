from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from mintflow.application.authentication.rate_limit import RateLimitReservation
from mintflow.infrastructure.persistence.models import AuthenticationRateLimitBucketRecord


class PostgreSQLAuthenticationRateLimiter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def reserve(self, reservation: RateLimitReservation) -> bool:
        table = AuthenticationRateLimitBucketRecord.__table__
        insert_statement = insert(AuthenticationRateLimitBucketRecord).values(
            dimension=reservation.dimension.value,
            key_digest=reservation.key_digest,
            window_started_at=reservation.window_started_at,
            count=1,
            expires_at=reservation.expires_at,
        )
        reservation_statement = insert_statement.on_conflict_do_update(
            index_elements=[table.c.dimension, table.c.key_digest, table.c.window_started_at],
            set_={"count": table.c.count + 1},
            where=table.c.count < reservation.limit,
        ).returning(table.c.count)
        allowed = self._session.execute(reservation_statement).scalar_one_or_none() is not None
        self._session.commit()
        return allowed
