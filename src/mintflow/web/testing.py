"""A small HTML reader for page tests (web design W10); never used to serve requests."""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

_MARKUP_WHITESPACE = re.compile(r"[ \t\n\r\f]+")

# Phrasing elements: adjacent ones join without a space, as a browser renders them.
_INLINE: frozenset[str] = frozenset(
    {"a", "abbr", "b", "code", "em", "i", "label", "small", "span", "strong", "sub", "sup", "time"}
)

# Elements that never have a closing tag.
_VOID: frozenset[str] = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)


@dataclass(eq=False)
class Element:
    tag: str
    attrs: dict[str, str | None]
    parent: "Element | None" = None
    # Text and child elements in document order.
    content: list["str | Element"] = field(default_factory=list)

    @property
    def children(self) -> list["Element"]:
        return [item for item in self.content if isinstance(item, Element)]

    @property
    def text(self) -> str:
        """All text inside, in document order, with markup whitespace collapsed.

        Only ASCII whitespace collapses: no-break and thin spaces are part of formatted values.
        """
        return _MARKUP_WHITESPACE.sub(" ", self._raw_text()).strip(" ")

    def _raw_text(self) -> str:
        parts = []
        for item in self.content:
            if isinstance(item, str):
                parts.append(item)
            elif item.tag in _INLINE:
                # Spaces at the edges of inline elements are real, as in "2026<span> · EUR</span>".
                parts.append(item._raw_text())
            else:
                # Block-level neighbours (cells, paragraphs) read as separate words.
                parts.append(f" {item._raw_text()} ")
        return "".join(parts)

    def iter(self) -> list["Element"]:
        found = [self]
        for child in self.children:
            found.extend(child.iter())
        return found

    def find_all(self, tag: str | None = None, **attrs: str) -> list["Element"]:
        """Matching descendants; ``data_x="y"`` matches ``data-x="y"``, ``class_`` ``class``."""
        wanted = {name.rstrip("_").replace("_", "-"): value for name, value in attrs.items()}
        return [
            element
            for element in self.iter()
            if (tag is None or element.tag == tag)
            and all(element.attrs.get(name) == value for name, value in wanted.items())
        ]

    def find(self, tag: str | None = None, **attrs: str) -> "Element":
        found = self.find_all(tag, **attrs)
        if len(found) != 1:
            raise AssertionError(f"expected one <{tag}> with {attrs}, found {len(found)}")
        return found[0]


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("#document", {})
        self._current = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = Element(tag, dict(attrs), parent=self._current)
        self._current.content.append(element)
        if tag not in _VOID:
            self._current = element

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._current.content.append(Element(tag, dict(attrs), parent=self._current))

    def handle_endtag(self, tag: str) -> None:
        element: Element | None = self._current
        while element is not None and element.tag != tag:
            element = element.parent
        if element is not None and element.parent is not None:
            self._current = element.parent

    def handle_data(self, data: str) -> None:
        self._current.content.append(data)


def parse_html(html: str) -> Element:
    builder = _Builder()
    builder.feed(html)
    builder.close()
    return builder.root
