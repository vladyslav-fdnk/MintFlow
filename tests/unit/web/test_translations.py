"""The Russian catalogue is complete and consistent, and languages are chosen as designed."""

import re
from collections.abc import Iterator

import pytest
from babel.messages.catalog import Message
from babel.messages.pofile import read_po

from mintflow.web.catalog import extract_messages
from mintflow.web.formatting import (
    LOCALE_DIRECTORY,
    _,
    category_label,
    display_locale,
    negotiate_language,
    ngettext,
    use_language,
)

PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


@pytest.fixture(autouse=True)
def _english_afterwards() -> Iterator[None]:
    yield
    use_language("en")


def _russian() -> dict[str, Message]:
    with (LOCALE_DIRECTORY / "ru.po").open("rb") as handle:
        catalogue = read_po(handle, locale="ru")
    return {
        (message.id if isinstance(message.id, str) else message.id[0]): message
        for message in catalogue
        if message.id
    }


def test_every_web_message_has_a_russian_translation() -> None:
    russian = _russian()

    for key in extract_messages():
        msgid = key if isinstance(key, str) else key[0]
        message = russian.get(msgid)
        assert message is not None, f"not in ru.po: {msgid!r}"
        assert not message.fuzzy, f"fuzzy: {msgid!r}"
        forms = message.string if isinstance(message.string, tuple) else (message.string,)
        assert all(forms), f"untranslated: {msgid!r}"


def test_translations_keep_every_placeholder() -> None:
    for msgid, message in _russian().items():
        forms = message.string if isinstance(message.string, tuple) else (message.string,)
        for form in forms:
            assert set(PLACEHOLDER.findall(str(form))) == set(PLACEHOLDER.findall(msgid)), msgid


def test_the_catalogue_has_no_stale_entries() -> None:
    extracted = {key if isinstance(key, str) else key[0] for key in extract_messages()}

    assert set(_russian()) == extracted


@pytest.mark.parametrize(
    ("count", "text"),
    [
        (1, "1 расход"),
        (2, "2 расхода"),
        (5, "5 расходов"),
        (11, "11 расходов"),
        (21, "21 расход"),
        (22, "22 расхода"),
    ],
)
def test_russian_plural_forms(count: int, text: str) -> None:
    use_language("ru")

    assert ngettext("{count} expense", "{count} expenses", count).format(count=count) == text


def test_english_is_the_source_and_unknown_languages_fall_back_to_it() -> None:
    use_language("de")

    assert _("You spent") == "You spent"
    assert ngettext("{count} expense", "{count} expenses", 2) == "{count} expenses"


def test_strings_and_category_labels_follow_the_language() -> None:
    use_language("ru")

    assert _("You spent") == "Вы потратили"
    assert category_label("groceries", "Groceries") == "Продукты"
    assert category_label("custom", "Custom name") == "Custom name"
    assert display_locale(None).language == "ru"


@pytest.mark.parametrize(
    ("header", "language"),
    [
        ("ru-RU,ru;q=0.9,en;q=0.8", "ru"),
        ("en-GB,en;q=0.9,ru;q=0.8", "en"),
        ("de-DE,de;q=0.9,ru;q=0.5", "ru"),
        ("de-DE", "en"),
        ("", "en"),
        ("ru;q=0.2,en;q=0.9", "en"),
        ("ru;q=bad", "en"),
    ],
)
def test_the_browsers_language_is_negotiated(header: str, language: str) -> None:
    assert negotiate_language(header) == language
