"""Web endpoints for linking Telegram (docs/telegram_client_design.md, T10).

Every route answers 404 when Telegram is not configured. Challenge reads and
confirmation are scoped to the initiating Web session; any other session gets
the same response as for an unknown challenge.
"""

import hmac
import logging
from datetime import datetime
from typing import Annotated, Final
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from mintflow.application.authentication import AuthenticatedWebSession
from mintflow.application.telegram import (
    ClaimTelegramLink,
    ConfirmTelegramLink,
    GetTelegramConnection,
    GetTelegramLinkStatus,
    IssueTelegramLinkChallenge,
    ResolveTelegramUser,
    TelegramConnection,
    UnlinkTelegram,
)
from mintflow.http.authentication import (
    AuthenticatedPrincipal,
    AuthenticatedPrincipalDependency,
    CsrfProtectedPrincipalDependency,
    DatabaseSession,
    authentication_security_headers,
)
from mintflow.infrastructure.persistence import (
    SqlAlchemyTelegramLinkRepository,
    SqlAlchemyTelegramUpdateLedger,
    SqlAlchemyUserRepository,
)
from mintflow.telegram import TelegramApiError, messages, parse_update
from mintflow.telegram.handler import TelegramUpdateHandler
from mintflow.telegram.runtime import TelegramRuntime

GENERIC_TELEGRAM_NOT_FOUND_MESSAGE: Final = "Not found."
TELEGRAM_SECRET_HEADER: Final = "X-Telegram-Bot-Api-Secret-Token"
MAX_WEBHOOK_BODY_BYTES: Final = 256 * 1024
GENERIC_TELEGRAM_LINK_FAILED_MESSAGE: Final = (
    "The Telegram account could not be connected. Please start again."
)

logger = logging.getLogger("mintflow.http.telegram")
router = APIRouter(prefix="/telegram", tags=["telegram"])


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=GENERIC_TELEGRAM_NOT_FOUND_MESSAGE,
        headers=authentication_security_headers(),
    )


async def get_telegram_runtime(request: Request) -> TelegramRuntime:
    runtime: TelegramRuntime | None = getattr(request.app.state, "telegram_runtime", None)
    if runtime is None:
        raise _not_found()
    return runtime


TelegramRuntimeDependency = Annotated[TelegramRuntime, Depends(get_telegram_runtime)]


async def get_telegram_link_repository(
    session: DatabaseSession, _runtime: TelegramRuntimeDependency
) -> SqlAlchemyTelegramLinkRepository:
    return SqlAlchemyTelegramLinkRepository(session)


TelegramLinkRepositoryDependency = Annotated[
    SqlAlchemyTelegramLinkRepository, Depends(get_telegram_link_repository)
]


def _web_session(principal: AuthenticatedPrincipal) -> AuthenticatedWebSession:
    return AuthenticatedWebSession(session_id=principal.web_session_id, user_id=principal.user_id)


def _json(body: dict[str, object], *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status_code, headers=authentication_security_headers())


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def _connection_json(connection: TelegramConnection | None) -> dict[str, object]:
    return {
        "connected": connection is not None,
        "telegram_display_name": connection.telegram_display_name if connection else None,
        "linked_at": _iso(connection.linked_at) if connection else None,
    }


@router.post("/link-challenges", status_code=201)
async def create_link_challenge(
    principal: CsrfProtectedPrincipalDependency,
    runtime: TelegramRuntimeDependency,
    repository: TelegramLinkRepositoryDependency,
) -> JSONResponse:
    issued = IssueTelegramLinkChallenge(
        repository=repository, bot_username=runtime.bot_username, clock=runtime.clock
    ).execute(session=_web_session(principal))
    return _json(
        {
            "challenge_id": str(issued.challenge_id),
            "deep_link": issued.deep_link,
            "expires_at": _iso(issued.expires_at),
        },
        status_code=201,
    )


@router.get("/link-challenges/{challenge_id}")
async def get_link_challenge(
    challenge_id: UUID,
    principal: AuthenticatedPrincipalDependency,
    runtime: TelegramRuntimeDependency,
    repository: TelegramLinkRepositoryDependency,
) -> JSONResponse:
    status = GetTelegramLinkStatus(repository=repository, clock=runtime.clock).execute(
        challenge_id=challenge_id, session=_web_session(principal)
    )
    if status is None:
        raise _not_found()
    return _json(
        {
            "challenge_id": str(status.challenge_id),
            "state": status.state.value,
            "expires_at": _iso(status.expires_at),
            "telegram_display_name": status.telegram_display_name,
        }
    )


@router.post("/link-challenges/{challenge_id}/confirm")
async def confirm_link_challenge(
    challenge_id: UUID,
    principal: CsrfProtectedPrincipalDependency,
    runtime: TelegramRuntimeDependency,
    repository: TelegramLinkRepositoryDependency,
) -> JSONResponse:
    connection = ConfirmTelegramLink(repository=repository, clock=runtime.clock).execute(
        challenge_id=challenge_id, session=_web_session(principal)
    )
    if connection is None:
        raise HTTPException(
            status_code=409,
            detail=GENERIC_TELEGRAM_LINK_FAILED_MESSAGE,
            headers=authentication_security_headers(),
        )
    # The link is committed; the bot's acknowledgement is best effort (design section 7.6).
    # In a private chat the chat id is the Telegram user id.
    try:
        runtime.bot_api.send_message(chat_id=connection.telegram_user_id, text=messages.LINKED)
    except TelegramApiError as error:
        logger.warning("telegram_link_ack_failed method=%s code=%s", error.method, error.error_code)
    return _json(_connection_json(connection))


@router.get("/connection")
async def get_connection(
    principal: AuthenticatedPrincipalDependency,
    _runtime: TelegramRuntimeDependency,
    repository: TelegramLinkRepositoryDependency,
) -> JSONResponse:
    connection = GetTelegramConnection(repository=repository).execute(user_id=principal.user_id)
    return _json(_connection_json(connection))


@router.delete("/connection", status_code=204)
async def delete_connection(
    principal: CsrfProtectedPrincipalDependency,
    runtime: TelegramRuntimeDependency,
    repository: TelegramLinkRepositoryDependency,
) -> Response:
    """Unlink immediately. Unlinking when nothing is connected is also 204."""
    UnlinkTelegram(repository=repository, clock=runtime.clock).execute(user_id=principal.user_id)
    return Response(status_code=204, headers=authentication_security_headers())


async def get_telegram_update_handler(
    session: DatabaseSession, runtime: TelegramRuntimeDependency
) -> TelegramUpdateHandler:
    links = SqlAlchemyTelegramLinkRepository(session)
    return TelegramUpdateHandler(
        ledger=SqlAlchemyTelegramUpdateLedger(session),
        resolve_user=ResolveTelegramUser(
            repository=links, user_repository=SqlAlchemyUserRepository(session)
        ),
        claim_link=ClaimTelegramLink(repository=links, clock=runtime.clock),
        bot_api=runtime.bot_api,
        web_origin=runtime.web_origin,
        clock=runtime.clock,
    )


TelegramUpdateHandlerDependency = Annotated[
    TelegramUpdateHandler, Depends(get_telegram_update_handler)
]


async def verify_webhook_secret(request: Request, runtime: TelegramRuntimeDependency) -> None:
    """Reject anything not sent by Telegram before the body is read (design T2)."""
    presented = request.headers.get(TELEGRAM_SECRET_HEADER, "")
    expected = runtime.webhook_secret.get_secret_value()
    if not hmac.compare_digest(presented.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Unauthorized.")


async def _read_bounded(request: Request) -> bytes | None:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_WEBHOOK_BODY_BYTES:
            return None
        body.extend(chunk)
    return bytes(body)


@router.post("/webhook", dependencies=[Depends(verify_webhook_secret)])
async def webhook(request: Request, handler: TelegramUpdateHandlerDependency) -> Response:
    """Always 200 for a verified request, so Telegram never redelivers committed work.

    Unreadable or oversized payloads are acknowledged and ignored. An unexpected
    failure while handling propagates as 500 with nothing committed, so Telegram's
    redelivery retries it.
    """
    body = await _read_bounded(request)
    update = parse_update(body) if body is not None else None
    if update is not None:
        handler.handle(update)
    return Response(status_code=200)
