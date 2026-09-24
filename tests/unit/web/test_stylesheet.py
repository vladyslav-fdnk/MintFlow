"""Every class a template uses for layout has a rule in the stylesheet.

Guards against a style block being lost while the stylesheet is edited: the page would still
render, only wrongly, and no other test would notice.
"""

import re
from pathlib import Path

from mintflow.web.rendering import STATIC_DIRECTORY

TEMPLATES = Path(STATIC_DIRECTORY).parent / "templates"
CSS = (STATIC_DIRECTORY / "css" / "app.css").read_text() + (
    STATIC_DIRECTORY / "css" / "noscript.css"
).read_text()

# Classes that only mark elements for the script, htmx, or tests, and need no style of their own.
HOOKS = frozenset(
    {
        "chart-body",
        "chart-card",
        "form-error",
        "link-status-region",
        "preset-label",
        "slice",
    }
)


def _template_classes() -> dict[str, set[str]]:
    used: dict[str, set[str]] = {}
    for template in sorted(TEMPLATES.glob("*.html")):
        for match in re.finditer(r'class="([^"]*)"', template.read_text()):
            # Drop Jinja expressions; a class built from one ("slice-{{ n }}") ends in "-".
            static = re.sub(r"\{[{%].*?[%}]\}", " ", match.group(1))
            for name in static.split():
                if not name.endswith("-"):
                    used.setdefault(name, set()).add(template.name)
    return used


def test_every_layout_class_has_a_rule() -> None:
    defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", CSS))

    missing = {
        name: sorted(templates)
        for name, templates in _template_classes().items()
        if name not in defined and name not in HOOKS
    }

    assert missing == {}


def test_the_hook_list_names_only_classes_still_in_use() -> None:
    assert HOOKS <= set(_template_classes())
