from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, cast
from uuid import UUID, uuid4

from mintflow.domain.capture import CurrencyCode
from mintflow.domain.user.preferences import Locale, Timezone, UILanguage

# Neutral starting defaults a User can change immediately after creation;
# never invented later as an implicit substitute for an explicit choice.
_DEFAULT_TIMEZONE: Final[Timezone] = Timezone("UTC")
_DEFAULT_UI_LANGUAGE: Final[UILanguage] = UILanguage("en")


class _Unset:
    __slots__ = ()


_UNSET: Final[_Unset] = _Unset()


class UserStatus(StrEnum):
    ACTIVE = "active"
    DEACTIVATED = "deactivated"


@dataclass(frozen=True, slots=True)
class User:
    id: UUID
    status: UserStatus
    created_at: datetime
    deactivated_at: datetime | None
    timezone: Timezone
    default_currency: CurrencyCode | None
    ui_language: UILanguage
    locale: Locale | None

    def __post_init__(self) -> None:
        if self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if self.deactivated_at is not None and self.deactivated_at.utcoffset() is None:
            raise ValueError("deactivated_at must be timezone-aware")
        if (self.status is UserStatus.ACTIVE) != (self.deactivated_at is None):
            raise ValueError("status and deactivated_at are inconsistent")
        if self.deactivated_at is not None and self.deactivated_at < self.created_at:
            raise ValueError("deactivated_at must not be before created_at")

    @classmethod
    def create(
        cls,
        *,
        now: datetime,
        timezone: Timezone = _DEFAULT_TIMEZONE,
        default_currency: CurrencyCode | None = None,
        ui_language: UILanguage = _DEFAULT_UI_LANGUAGE,
        locale: Locale | None = None,
    ) -> "User":
        if now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return cls(
            id=uuid4(),
            status=UserStatus.ACTIVE,
            created_at=now.astimezone(UTC),
            deactivated_at=None,
            timezone=timezone,
            default_currency=default_currency,
            ui_language=ui_language,
            locale=locale,
        )

    def deactivate(self, *, now: datetime) -> "User":
        if now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if self.status is UserStatus.DEACTIVATED:
            return self
        return replace(
            self,
            status=UserStatus.DEACTIVATED,
            deactivated_at=now.astimezone(UTC),
        )

    def update_preferences(
        self,
        *,
        timezone: str | None = None,
        default_currency: str | None | _Unset = _UNSET,
        ui_language: str | None = None,
        locale: str | None | _Unset = _UNSET,
    ) -> "User":
        """Validate every provided value before applying any of them.

        Each raw input is converted through its value object first; if any
        conversion fails, this raises before ``replace`` runs, so no field
        is partially applied. ``default_currency``/``locale`` are optional
        and nullable, so they use a sentinel to distinguish "not provided"
        (keep the existing value) from an explicit ``None`` (clear it).
        """
        new_timezone = Timezone(timezone) if timezone is not None else self.timezone
        new_ui_language = UILanguage(ui_language) if ui_language is not None else self.ui_language
        new_default_currency = self.default_currency
        if default_currency is not _UNSET:
            raw_currency = cast("str | None", default_currency)
            new_default_currency = CurrencyCode(raw_currency) if raw_currency is not None else None
        new_locale = self.locale
        if locale is not _UNSET:
            raw_locale = cast("str | None", locale)
            new_locale = Locale(raw_locale) if raw_locale is not None else None

        return replace(
            self,
            timezone=new_timezone,
            default_currency=new_default_currency,
            ui_language=new_ui_language,
            locale=new_locale,
        )
