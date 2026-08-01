from sqlalchemy.orm import Session

from mintflow.application.authentication.login_challenge import LoginChallenge
from mintflow.infrastructure.persistence.models import LoginChallengeRecord


class SqlAlchemyLoginChallengeStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, challenge: LoginChallenge) -> None:
        self._session.add(
            LoginChallengeRecord(
                id=challenge.id,
                canonical_email=challenge.canonical_email,
                token_hash=challenge.token_hash,
                issued_at=challenge.issued_at,
                expires_at=challenge.expires_at,
                consumed_at=challenge.consumed_at,
                return_target=challenge.return_target,
            )
        )
        self._session.commit()
