"""Attach the Web Client to the FastAPI application."""

from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException

from mintflow.web import (
    account_deletion,
    dashboard,
    expense_detail,
    expenses,
    pages,
    settings,
    sign_in,
)
from mintflow.web.rendering import STATIC_PATH, RequestPreferencesMiddleware, static_files


def install_web(application: FastAPI) -> None:
    application.include_router(sign_in.router)
    application.include_router(pages.router)
    application.include_router(dashboard.router)
    application.include_router(expenses.router)
    application.include_router(expense_detail.router)
    application.include_router(settings.router)
    application.include_router(account_deletion.router)
    application.mount(STATIC_PATH, static_files(), name="static")
    application.add_exception_handler(pages.SignInRequired, pages.sign_in_required)
    application.add_exception_handler(StarletteHTTPException, pages.http_error)
    application.add_exception_handler(Exception, pages.server_error)
    application.add_middleware(RequestPreferencesMiddleware)
