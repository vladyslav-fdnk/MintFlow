import pytest

from mintflow.domain.user import Locale, Timezone, UILanguage


def test_timezone_accepts_a_known_iana_zone() -> None:
    assert Timezone("Europe/Warsaw").value == "Europe/Warsaw"
    assert Timezone("UTC").value == "UTC"


def test_timezone_rejects_an_unknown_zone() -> None:
    with pytest.raises(ValueError, match="unknown IANA timezone"):
        Timezone("Not/AZone")


def test_locale_normalizes_subtag_casing() -> None:
    assert Locale("EN-us").value == "en-US"
    assert Locale("en").value == "en"


def test_locale_rejects_malformed_tags() -> None:
    with pytest.raises(ValueError, match="invalid BCP-47 locale tag"):
        Locale("")
    with pytest.raises(ValueError, match="invalid BCP-47 locale tag"):
        Locale("e")
    with pytest.raises(ValueError, match="invalid BCP-47 locale tag"):
        Locale("en_US")


def test_ui_language_normalizes_to_lowercase() -> None:
    assert UILanguage("EN").value == "en"


def test_ui_language_rejects_non_two_letter_input() -> None:
    with pytest.raises(ValueError, match="invalid UI language code"):
        UILanguage("eng")
    with pytest.raises(ValueError, match="invalid UI language code"):
        UILanguage("")
