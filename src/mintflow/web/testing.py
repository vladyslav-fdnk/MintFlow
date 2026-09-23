"""A small HTML reader for page tests (web design W10); never used to serve requests."""

from dataclasses import dataclass, field
from html.parser import HTMLParser

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
        """All text inside, in document order, whitespace-collapsed."""
        parts = [item if isinstance(item, str) else item.text for item in self.content]
        return " ".join(" ".join(parts).split())

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
