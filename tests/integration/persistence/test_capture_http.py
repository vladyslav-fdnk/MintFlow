import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mintflow.application.authentication import hash_token
from mintflow.config import Settings
from mintflow.domain.user import UserStatus
from mintflow.http.authentication import (
    AUTHENTICATED_SESSION_COOKIE_NAME,
    CSRF_HEADER_NAME,
    AuthenticationRuntime,
)
from mintflow.http.capture import CaptureRuntime
from mintflow.infrastructure.persistence.models import (
    CaptureDraftRecord,
    ExpenseChangeRecordModel,
    ExpenseRecord,
    UserRecord,
    WebSessionRecord,
)
from mintflow.main import create_app

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
ORIGIN = "https://app.mintflow.test"


def _settings(database_url: str) -> Settings:
    return Settings(
        environment="test",
        log_level="CRITICAL",
        database_url=SecretStr(database_url),
        authentication_rate_limit_key=SecretStr("integration-rate-limit-key"),
        authentication_csrf_signing_key=SecretStr("integration-csrf-signing-key"),
        authentication_web_origin=ORIGIN,
        authentication_return_targets=frozenset({"dashboard"}),
        email_backend=None,
    )


def _application(database_url: str) -> FastAPI:
    application = create_app(_settings(database_url))
    auth_runtime: AuthenticationRuntime = application.state.authentication_runtime
    application.state.authentication_runtime = AuthenticationRuntime(
        session_factory=auth_runtime.session_factory,
        email_sender=auth_runtime.email_sender,
        link_builder=auth_runtime.link_builder,
        rate_limit_digester=auth_runtime.rate_limit_digester,
        csrf_digester=auth_runtime.csrf_digester,
        clock=lambda: NOW,
    )
    application.state.capture_runtime = CaptureRuntime(clock=lambda: NOW)
    return application


def _add_user(session: Session) -> UserRecord:
    user = UserRecord(status=UserStatus.ACTIVE.value, created_at=NOW)
    session.add(user)
    session.commit()
    return user


def _add_session(session: Session, *, user_id: UUID, secret: str) -> None:
    session.add(
        WebSessionRecord(
            user_id=user_id,
            secret_hash=hash_token(secret),
            issued_at=NOW,
            expires_at=NOW + timedelta(days=1),
        )
    )
    session.commit()


def _headers(application: FastAPI, *, secret: str) -> dict[str, str]:
    runtime: AuthenticationRuntime = application.state.authentication_runtime
    token = runtime.csrf_digester.derive(session_secret=secret)
    return {CSRF_HEADER_NAME: token, "Origin": ORIGIN}


async def _request(
    application: FastAPI,
    method: str,
    path: str,
    *,
    secret: str,
    csrf: bool = True,
    json: dict[str, object] | None = None,
) -> Response:
    cookies = {AUTHENTICATED_SESSION_COOKIE_NAME: secret}
    headers = _headers(application, secret=secret) if csrf else {}
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url=ORIGIN) as client:
        return await client.request(method, path, cookies=cookies, headers=headers, json=json)


@pytest.mark.anyio
async def test_full_happy_path_end_to_end_against_real_persistence(
    db_session: Session, migrated_database_url: str
) -> None:
    user = _add_user(db_session)
    secret = "A" * 43
    _add_session(db_session, user_id=user.id, secret=secret)
    application = _application(migrated_database_url)

    start = await _request(application, "POST", "/capture/drafts", secret=secret)
    assert start.status_code == 201
    draft_id = start.json()["id"]

    edited = await _request(
        application,
        "PATCH",
        f"/capture/drafts/{draft_id}",
        secret=secret,
        json={"amount_minor_units": 2500, "currency": "EUR"},
    )
    assert edited.status_code == 200
    assert edited.json()["revision"] == 1

    ready = await _request(application, "POST", f"/capture/drafts/{draft_id}/ready", secret=secret)
    # not confirmable yet (no transaction date) -- ready still succeeds, it
    # only requires reachability from collecting, not confirmability.
    assert ready.status_code == 200
    assert ready.json()["state"] == "ready_for_review"

    view = await _request(application, "GET", f"/capture/drafts/{draft_id}", secret=secret)
    assert view.status_code == 200
    assert view.json()["amount_minor_units"] == 2500
    assert view.json()["currency"] == "EUR"

    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_ownership_scoping_end_to_end(
    db_session: Session, migrated_database_url: str
) -> None:
    owner = _add_user(db_session)
    stranger = _add_user(db_session)
    owner_secret = "B" * 43
    stranger_secret = "C" * 43
    _add_session(db_session, user_id=owner.id, secret=owner_secret)
    _add_session(db_session, user_id=stranger.id, secret=stranger_secret)
    application = _application(migrated_database_url)

    start = await _request(application, "POST", "/capture/drafts", secret=owner_secret)
    draft_id = start.json()["id"]

    stranger_view = await _request(
        application, "GET", f"/capture/drafts/{draft_id}", secret=stranger_secret
    )
    unknown_view = await _request(
        application, "GET", f"/capture/drafts/{uuid4()}", secret=stranger_secret
    )

    assert stranger_view.status_code == unknown_view.status_code == 404
    assert stranger_view.json() == unknown_view.json()

    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_list_categories_returns_the_seeded_system_catalogue(
    db_session: Session, migrated_database_url: str
) -> None:
    user = _add_user(db_session)
    secret = "E" * 43
    _add_session(db_session, user_id=user.id, secret=secret)
    application = _application(migrated_database_url)

    response = await _request(application, "GET", "/capture/categories", secret=secret, csrf=False)

    assert response.status_code == 200
    keys = {category["key"] for category in response.json()}
    assert "uncategorized" in keys
    assert "groceries" in keys
    assert len(keys) == 13

    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_concurrent_patch_requests_leave_a_consistent_final_revision(
    db_session: Session, migrated_database_url: str
) -> None:
    user = _add_user(db_session)
    secret = "D" * 43
    _add_session(db_session, user_id=user.id, secret=secret)
    application = _application(migrated_database_url)
    start = await _request(application, "POST", "/capture/drafts", secret=secret)
    draft_id = start.json()["id"]

    # PATCH intentionally does not lock the draft row (only CAPTURE-08's
    # confirmation flow does), so two genuinely concurrent PATCH requests
    # race: both read the same revision, then each writes, and whichever
    # commits last wins -- a classic, expected lost update, not corruption.
    # Force that exact race with a synchronized clock: both requests must
    # finish reading the draft and reach the clock call before either can
    # proceed to write.
    barrier = Barrier(2)

    def synchronized_clock() -> datetime:
        barrier.wait(timeout=5)
        return NOW

    application.state.capture_runtime = CaptureRuntime(clock=synchronized_clock)

    def worker(value: str) -> Response:
        return asyncio.run(
            _request(
                application,
                "PATCH",
                f"/capture/drafts/{draft_id}",
                secret=secret,
                json={"note": value},
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(worker, ["first", "second"]))

    assert all(response.status_code == 200 for response in results)
    db_session.expire_all()
    record = db_session.scalar(
        select(CaptureDraftRecord).where(CaptureDraftRecord.id == UUID(draft_id))
    )
    assert record is not None
    # Exactly one edit survives (a well-defined final revision), never a
    # torn/corrupted mix of the two concurrent writes.
    assert record.revision == 1
    assert record.note in ("first", "second")

    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_full_manual_capture_to_confirmation_loop_end_to_end(
    db_session: Session, migrated_database_url: str
) -> None:
    user = _add_user(db_session)
    secret = "F" * 43
    _add_session(db_session, user_id=user.id, secret=secret)
    application = _application(migrated_database_url)

    start = await _request(application, "POST", "/capture/drafts", secret=secret)
    draft_id = start.json()["id"]

    edited = await _request(
        application,
        "PATCH",
        f"/capture/drafts/{draft_id}",
        secret=secret,
        json={
            "amount_minor_units": 4200,
            "currency": "USD",
            "transaction_date": NOW.date().isoformat(),
            "merchant": "Coffee Shop",
            "category_key": "groceries",
            "note": "Team lunch",
        },
    )
    assert edited.status_code == 200

    ready = await _request(application, "POST", f"/capture/drafts/{draft_id}/ready", secret=secret)
    assert ready.status_code == 200

    confirm = await _request(
        application, "POST", f"/capture/drafts/{draft_id}/confirm", secret=secret
    )
    assert confirm.status_code == 200
    expense_id = confirm.json()["id"]
    assert confirm.json()["amount_minor_units"] == 4200
    assert confirm.json()["currency"] == "USD"
    assert confirm.json()["merchant"] == "Coffee Shop"
    assert confirm.json()["category_key"] == "groceries"
    assert confirm.json()["note"] == "Team lunch"

    view = await _request(application, "GET", f"/capture/expenses/{expense_id}", secret=secret)
    assert view.status_code == 200
    assert view.json() == confirm.json()

    db_session.expire_all()
    draft_record = db_session.get(CaptureDraftRecord, UUID(draft_id))
    assert draft_record is not None
    assert draft_record.state == "confirmed"
    assert draft_record.expense_id == UUID(expense_id)

    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_concurrent_double_confirm_through_real_http_routes_creates_one_expense(
    db_session: Session, migrated_database_url: str
) -> None:
    user = _add_user(db_session)
    secret = "G" * 43
    _add_session(db_session, user_id=user.id, secret=secret)
    application = _application(migrated_database_url)

    start = await _request(application, "POST", "/capture/drafts", secret=secret)
    draft_id = start.json()["id"]
    await _request(
        application,
        "PATCH",
        f"/capture/drafts/{draft_id}",
        secret=secret,
        json={"amount_minor_units": 1000, "currency": "USD"},
    )
    await _request(
        application,
        "PATCH",
        f"/capture/drafts/{draft_id}",
        secret=secret,
        json={"transaction_date": NOW.date().isoformat()},
    )
    await _request(application, "POST", f"/capture/drafts/{draft_id}/ready", secret=secret)

    # The confirmation use case locks the draft row (CAPTURE-08) and calls
    # the clock only after acquiring that lock. A second request submitted
    # while the first is still holding it must genuinely block inside the
    # database on the row lock -- it never even reaches the clock. So the
    # synchronization point is: let request A pause (holding the lock)
    # right after its clock call, confirm request B is actually blocked
    # (not merely slow), then release A and let B proceed to the
    # already-confirmed idempotent-return path.
    started = Event()
    release = Event()

    def blocking_clock() -> datetime:
        started.set()
        assert release.wait(timeout=5)
        return NOW

    def worker(*, clock: Callable[[], datetime]) -> Response:
        application.state.capture_runtime = CaptureRuntime(clock=clock)
        return asyncio.run(
            _request(application, "POST", f"/capture/drafts/{draft_id}/confirm", secret=secret)
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_a = executor.submit(worker, clock=blocking_clock)
        assert started.wait(timeout=5)
        future_b = executor.submit(worker, clock=lambda: NOW)
        with pytest.raises(TimeoutError):
            future_b.result(timeout=0.3)
        release.set()
        response_a = future_a.result(timeout=5)
        response_b = future_b.result(timeout=5)

    results = [response_a, response_b]
    assert all(response.status_code == 200 for response in results), [
        (response.status_code, response.text) for response in results
    ]
    expense_ids = {response.json()["id"] for response in results}
    assert len(expense_ids) == 1

    db_session.expire_all()
    assert (
        db_session.scalar(
            select(ExpenseRecord).where(ExpenseRecord.capture_draft_id == UUID(draft_id))
        )
        is not None
    )

    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ExpenseRecord)
            .where(ExpenseRecord.capture_draft_id == UUID(draft_id))
        )
        == 1
    )

    application.state.database_engine.dispose()


@pytest.mark.anyio
async def test_edit_confirmed_expense_end_to_end_records_the_change(
    db_session: Session, migrated_database_url: str
) -> None:
    user = _add_user(db_session)
    secret = "H" * 43
    _add_session(db_session, user_id=user.id, secret=secret)
    application = _application(migrated_database_url)

    draft_id = (await _request(application, "POST", "/capture/drafts", secret=secret)).json()["id"]
    await _request(
        application,
        "PATCH",
        f"/capture/drafts/{draft_id}",
        secret=secret,
        json={
            "amount_minor_units": 4200,
            "currency": "USD",
            "transaction_date": NOW.date().isoformat(),
            "merchant": "Coffee Shop",
            "note": "Team lunch",
        },
    )
    await _request(application, "POST", f"/capture/drafts/{draft_id}/ready", secret=secret)
    confirm = await _request(
        application, "POST", f"/capture/drafts/{draft_id}/confirm", secret=secret
    )
    expense_id = confirm.json()["id"]

    rejected_csrf = await _request(
        application,
        "PATCH",
        f"/capture/expenses/{expense_id}",
        secret=secret,
        csrf=False,
        json={"note": "sneaky"},
    )
    edited = await _request(
        application,
        "PATCH",
        f"/capture/expenses/{expense_id}",
        secret=secret,
        json={
            "amount_minor_units": 3900,
            "currency": "USD",
            "merchant": None,
            "category_key": "health",
        },
    )
    view = await _request(application, "GET", f"/capture/expenses/{expense_id}", secret=secret)

    assert rejected_csrf.status_code == 403
    assert edited.status_code == 200
    assert view.json() == edited.json()
    assert edited.json()["note"] == "Team lunch"
    db_session.expire_all()
    record = db_session.get(ExpenseRecord, UUID(expense_id))
    assert record is not None
    assert (record.amount_minor_units, record.merchant_name, record.category_key, record.note) == (
        3900,
        None,
        "health",
        "Team lunch",
    )
    changes = db_session.scalars(
        select(ExpenseChangeRecordModel).where(
            ExpenseChangeRecordModel.expense_id == UUID(expense_id)
        )
    ).all()
    assert [(change.change_type, change.actor_user_id) for change in changes] == [
        ("edited", user.id)
    ]
    assert changes[0].changes == {
        "amount_minor_units": {"old": 4200, "new": 3900},
        "merchant": {"old": "Coffee Shop", "new": None},
        "category_key": {"old": "uncategorized", "new": "health"},
    }

    application.state.database_engine.dispose()
