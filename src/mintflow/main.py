from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from mintflow.application.authentication.login_challenge import EmailSender
from mintflow.config import Settings, get_settings
from mintflow.http.authentication import (
    AuthenticationConfigurationError,
    build_authentication_runtime,
)
from mintflow.http.authentication import (
    router as authentication_router,
)
from mintflow.infrastructure.email import create_local_email_sender
from mintflow.infrastructure.persistence import create_database_engine, create_session_factory
from mintflow.logging import configure_logging
from mintflow.readiness import is_postgresql_ready


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the MintFlow application shell."""

    runtime_settings = settings or get_settings()
    configure_logging(runtime_settings.log_level)
    try:
        with ExitStack() as composition_cleanup:
            authentication_email_sender: EmailSender | None = None
            if runtime_settings.email_backend == "mailpit":
                authentication_email_sender = create_local_email_sender(runtime_settings)
            database_engine = create_database_engine(
                runtime_settings.database_url.get_secret_value()
            )
            composition_cleanup.callback(database_engine.dispose)
            authentication_runtime = build_authentication_runtime(
                settings=runtime_settings,
                session_factory=create_session_factory(database_engine),
                email_sender=authentication_email_sender,
            )
            composition_cleanup.pop_all()
    except (SQLAlchemyError, TypeError, ValueError):
        raise AuthenticationConfigurationError(
            "authentication runtime configuration is invalid"
        ) from None
    docs_url = "/docs" if runtime_settings.enable_api_docs else None
    openapi_url = "/openapi.json" if runtime_settings.enable_api_docs else None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            database_engine.dispose()

    application = FastAPI(
        title="MintFlow Platform",
        version="0.1.0",
        docs_url=docs_url,
        redoc_url=None,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )
    application.state.authentication_email_sender = authentication_email_sender
    application.state.authentication_runtime = authentication_runtime
    application.state.database_engine = database_engine
    application.include_router(authentication_router)

    @application.get("/health/live", tags=["health"])
    async def liveness() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @application.get("/health/ready", tags=["health"])
    async def readiness() -> JSONResponse:
        database_ready = await is_postgresql_ready(runtime_settings.database_url.get_secret_value())
        status_code = 200 if database_ready else 503
        status = "ready" if database_ready else "not_ready"
        dependency_status = "available" if database_ready else "unavailable"
        return JSONResponse(
            {
                "status": status,
                "dependencies": {"postgresql": dependency_status},
            },
            status_code=status_code,
        )

    return application
