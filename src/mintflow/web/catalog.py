"""The Web Client's message catalogue: extraction and updating (web design W14).

Development tooling, used by ``make translations`` and by the tests that keep every language
complete; pages only read the .po files through ``web.formatting``.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

from babel.messages.catalog import Catalog
from babel.messages.extract import extract
from babel.messages.pofile import read_po, write_po
from jinja2.ext import babel_extract

from mintflow.web.formatting import LOCALE_DIRECTORY, SUPPORTED_LANGUAGES

WEB_DIRECTORY: Final = Path(__file__).parent
KEYWORDS: Final = {"_": None, "N_": None, "ngettext": (1, 2)}
SOURCE_LANGUAGE: Final = "en"


def extract_messages() -> dict[str | tuple[str, str], list[tuple[str, int]]]:
    """Every translatable message in the Web Client, with where it appears."""
    found: dict[str | tuple[str, str], list[tuple[str, int]]] = {}
    for path, method in _sources():
        with path.open("rb") as handle:
            for line, message, _comments, _context in extract(
                method, handle, keywords=KEYWORDS, options={"trimmed": "true"}
            ):
                key = message if isinstance(message, str) else (message[0], message[1])
                found.setdefault(key, []).append((str(path.relative_to(WEB_DIRECTORY)), line))
    return found


def _sources() -> Iterator[tuple[Path, Any]]:
    for path in sorted(WEB_DIRECTORY.rglob("*.py")):
        if path.name != "catalog.py":
            yield path, "python"
    for path in sorted((WEB_DIRECTORY / "templates").rglob("*.html")):
        yield path, babel_extract


def update_catalogues() -> list[str]:
    """Rewrite each translation's .po: new messages added, gone ones dropped, translations kept.

    Returns the messages still untranslated, per language, for the caller to report.
    """
    messages = extract_messages()
    missing: list[str] = []
    for language in SUPPORTED_LANGUAGES:
        if language == SOURCE_LANGUAGE:
            continue
        path = LOCALE_DIRECTORY / f"{language}.po"
        old = _read(path, language)
        catalogue = Catalog(locale=language, project="MintFlow", fuzzy=False)
        for key, locations in sorted(messages.items(), key=lambda item: str(item[0])):
            previous = old.get(key if isinstance(key, str) else key[0]) if old else None
            string = (
                previous.string
                if previous is not None
                else ("" if isinstance(key, str) else ("",) * catalogue.num_plurals)
            )
            catalogue.add(key, string=string, locations=locations)
            if not string or (isinstance(string, tuple) and not all(string)):
                missing.append(f"{language}: {key}")
        LOCALE_DIRECTORY.mkdir(exist_ok=True)
        with path.open("wb") as handle:
            write_po(handle, catalogue, width=100, omit_header=False, sort_output=True)
    return missing


def _read(path: Path, language: str) -> Catalog | None:
    if not path.is_file():
        return None
    with path.open("rb") as handle:
        return read_po(handle, locale=language)


if __name__ == "__main__":
    for line in update_catalogues():
        print("untranslated", line)
