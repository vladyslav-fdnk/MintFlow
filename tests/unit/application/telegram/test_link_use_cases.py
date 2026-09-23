from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from mintflow.application.authentication import AuthenticatedWebSession
from mintflow.application.authentication.tokens import GeneratedToken, hash_token
from mintflow.application.telegram import (
    ClaimTelegramLink,
    ConfirmTelegramLink,
    GetTelegramLinkStatus,
    IssueTelegramLinkChallenge,
    ResolveTelegramUser,
    TelegramConnection,
    TelegramLinkChallenge,
    TelegramLinkState,
    UnlinkTelegram,
)
from mintflow.domain.user import User

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
RAW_TOKEN = "raw-token_ABC-123"
SESSION = AuthenticatedWebSession(session_id=uuid4(), user_id=uuid4())


def _challenge(**overrides: object) -> TelegramLinkChallenge:
    values: dict[str, object] = {
        "id": uuid4(),
        "initiating_user_id": SESSION.user_id,
        "initiating_web_session_id": SESSION.session_id,
        "issued_at": NOW,
        "expires_at": NOW + timedelta(minutes=5),
        "claimed_at": None,
        "claimed_telegram_user_id": None,
        "claimed_telegram_display_name": None,
        "confirmed_at": None,
    }
    values.update(overrides)
    return TelegramLinkChallenge(**values)  # type: ignore[arg-type]


class FakeRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.challenge: TelegramLinkChallenge | None = None
        self.claim_result: TelegramLinkChallenge | None = None
        self.connection: TelegramConnection | None = None
        self.unlink_result = True

    def create_challenge(self, **kwargs: object) -> TelegramLinkChallenge:
        self.calls.append(("create_challenge", kwargs))
        return _challenge(expires_at=kwargs["expires_at"])

    def claim(self, **kwargs: object) -> TelegramLinkChallenge | None:
        self.calls.append(("claim", kwargs))
        return self.claim_result

    def challenge_for_session(
        self, *, challenge_id: UUID, web_session_id: UUID
    ) -> TelegramLinkChallenge | None:
        self.calls.append(("challenge_for_session", {"web_session_id": web_session_id}))
        return self.challenge

    def confirm(self, **kwargs: object) -> TelegramConnection | None:
        self.calls.append(("confirm", kwargs))
        return self.connection

    def active_connection_for_user(self, *, user_id: UUID) -> TelegramConnection | None:
        return self.connection

    def active_connection_for_telegram_user(
        self, *, telegram_user_id: int
    ) -> TelegramConnection | None:
        return self.connection

    def unlink(self, **kwargs: object) -> bool:
        self.calls.append(("unlink", kwargs))
        return self.unlink_result


class FakeUsers:
    def __init__(self, user: User | None) -> None:
        self.user = user

    def get(self, user_id: UUID) -> User | None:
        return self.user if self.user is not None and self.user.id == user_id else None


def test_issue_stores_only_the_digest_and_builds_a_deep_link() -> None:
    repository = FakeRepository()
    token = GeneratedToken(raw=RAW_TOKEN, digest=hash_token(RAW_TOKEN))
    use_case = IssueTelegramLinkChallenge(
        repository=repository,
        bot_username="mintflow_bot",
        clock=lambda: NOW,
        token_generator=lambda: token,
    )

    issued = use_case.execute(session=SESSION)

    assert issued.deep_link == f"https://t.me/mintflow_bot?start={RAW_TOKEN}"
    assert issued.expires_at == NOW + timedelta(minutes=5)
    [(_, stored)] = repository.calls
    assert stored == {
        "token_hash": hash_token(RAW_TOKEN),
        "user_id": SESSION.user_id,
        "web_session_id": SESSION.session_id,
        "issued_at": NOW,
        "expires_at": NOW + timedelta(minutes=5),
    }
    assert RAW_TOKEN not in repr(stored)


def test_issued_tokens_fit_telegrams_start_payload_rules() -> None:
    repository = FakeRepository()
    use_case = IssueTelegramLinkChallenge(
        repository=repository, bot_username="mintflow_bot", clock=lambda: NOW
    )

    payload = use_case.execute(session=SESSION).deep_link.split("?start=", 1)[1]

    assert 43 <= len(payload) <= 64
    assert set(payload) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")


def test_claim_hashes_the_payload_and_reports_success() -> None:
    repository = FakeRepository()
    repository.claim_result = _challenge(claimed_at=NOW, claimed_telegram_user_id=7)

    claimed = ClaimTelegramLink(repository=repository, clock=lambda: NOW).execute(
        payload=RAW_TOKEN, telegram_user_id=7, display_name="Ada (@ada)"
    )

    assert claimed is True
    [(_, arguments)] = repository.calls
    assert arguments == {
        "token_hash": hash_token(RAW_TOKEN),
        "telegram_user_id": 7,
        "display_name": "Ada (@ada)",
        "now": NOW,
    }


def test_claim_failure_is_a_single_generic_outcome() -> None:
    repository = FakeRepository()

    assert (
        ClaimTelegramLink(repository=repository, clock=lambda: NOW).execute(
            payload=RAW_TOKEN, telegram_user_id=7, display_name=None
        )
        is False
    )


@pytest.mark.parametrize("payload", ["", "has space", "x" * 65, "semi;colon", "ümlaut"])
def test_malformed_payloads_never_reach_the_repository(payload: str) -> None:
    repository = FakeRepository()
    repository.claim_result = _challenge()

    claimed = ClaimTelegramLink(repository=repository, clock=lambda: NOW).execute(
        payload=payload, telegram_user_id=7, display_name=None
    )

    assert claimed is False
    assert repository.calls == []


@pytest.mark.parametrize(
    ("challenge", "at", "state", "display_name"),
    [
        (_challenge(), NOW, TelegramLinkState.WAITING_FOR_TELEGRAM, None),
        (
            _challenge(
                claimed_at=NOW, claimed_telegram_user_id=7, claimed_telegram_display_name="Ada"
            ),
            NOW + timedelta(minutes=1),
            TelegramLinkState.AWAITING_CONFIRMATION,
            "Ada",
        ),
        (
            _challenge(
                claimed_at=NOW,
                claimed_telegram_user_id=7,
                claimed_telegram_display_name="Ada",
                confirmed_at=NOW + timedelta(minutes=1),
            ),
            NOW + timedelta(minutes=30),
            TelegramLinkState.CONNECTED,
            "Ada",
        ),
        (
            _challenge(
                claimed_at=NOW, claimed_telegram_user_id=7, claimed_telegram_display_name="Ada"
            ),
            NOW + timedelta(minutes=5),
            TelegramLinkState.EXPIRED,
            None,
        ),
    ],
)
def test_status_maps_every_state(
    challenge: TelegramLinkChallenge,
    at: datetime,
    state: TelegramLinkState,
    display_name: str | None,
) -> None:
    repository = FakeRepository()
    repository.challenge = challenge

    status = GetTelegramLinkStatus(repository=repository, clock=lambda: at).execute(
        challenge_id=challenge.id, session=SESSION
    )

    assert status is not None
    assert (status.state, status.telegram_display_name) == (state, display_name)
    assert repository.calls == [("challenge_for_session", {"web_session_id": SESSION.session_id})]


def test_status_for_another_session_is_none() -> None:
    assert (
        GetTelegramLinkStatus(repository=FakeRepository(), clock=lambda: NOW).execute(
            challenge_id=uuid4(), session=SESSION
        )
        is None
    )


def test_confirm_is_bound_to_the_calling_session() -> None:
    repository = FakeRepository()
    challenge_id = uuid4()

    ConfirmTelegramLink(repository=repository, clock=lambda: NOW).execute(
        challenge_id=challenge_id, session=SESSION
    )

    assert repository.calls == [
        (
            "confirm",
            {"challenge_id": challenge_id, "web_session_id": SESSION.session_id, "now": NOW},
        )
    ]


def test_unlink_uses_the_clock_in_utc() -> None:
    repository = FakeRepository()
    local = NOW.astimezone(ZoneInfo("Asia/Tokyo"))

    assert UnlinkTelegram(repository=repository, clock=lambda: local).execute(
        user_id=SESSION.user_id
    )
    [(_, arguments)] = repository.calls
    now = arguments["now"]
    assert isinstance(now, datetime)
    assert now == NOW
    assert now.tzinfo is UTC


def test_naive_clocks_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        UnlinkTelegram(repository=FakeRepository(), clock=lambda: datetime(2026, 9, 1)).execute(
            user_id=SESSION.user_id
        )


def _connection(user_id: UUID) -> TelegramConnection:
    return TelegramConnection(
        id=uuid4(),
        user_id=user_id,
        telegram_user_id=7,
        telegram_display_name=None,
        linked_at=NOW,
        unlinked_at=None,
    )


def test_resolve_returns_the_active_user_of_an_active_connection() -> None:
    user = User.create(now=NOW)
    repository = FakeRepository()
    repository.connection = _connection(user.id)

    resolved = ResolveTelegramUser(repository=repository, user_repository=FakeUsers(user)).execute(
        telegram_user_id=7
    )

    assert resolved == user


def test_resolve_refuses_unlinked_and_deactivated_accounts() -> None:
    user = User.create(now=NOW)
    deactivated = user.deactivate(now=NOW)
    unlinked = FakeRepository()
    linked = FakeRepository()
    linked.connection = _connection(user.id)

    assert (
        ResolveTelegramUser(repository=unlinked, user_repository=FakeUsers(user)).execute(
            telegram_user_id=7
        )
        is None
    )
    assert (
        ResolveTelegramUser(repository=linked, user_repository=FakeUsers(deactivated)).execute(
            telegram_user_id=7
        )
        is None
    )
