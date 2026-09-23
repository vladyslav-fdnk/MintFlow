"""The server-rendered Web Client (docs/web_client_design.md)."""

from mintflow.web.app import install_web
from mintflow.web.pages import PagePrincipalDependency, SignInRequired

__all__ = ["PagePrincipalDependency", "SignInRequired", "install_web"]
