"""Every text and control colour pair in the stylesheet meets WCAG 2.2 AA (web design W11)."""

import re

import pytest

from mintflow.web.rendering import STATIC_DIRECTORY

CSS = (STATIC_DIRECTORY / "css" / "app.css").read_text()
NORMAL_TEXT = 4.5
LARGE_TEXT_OR_UI = 3.0


def _tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-f]{6});", block))


def _theme_blocks() -> dict[str, dict[str, str]]:
    light = _tokens(CSS[CSS.index(":root {") : CSS.index("@media (prefers-color-scheme: dark)")])
    dark_start = CSS.index("@media (prefers-color-scheme: dark)")
    dark = {**light, **_tokens(CSS[dark_start : CSS.index("/* --- base", dark_start)])}
    return {"light": light, "dark": dark}


THEMES = _theme_blocks()


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground: str, background: str) -> float:
    high, low = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


# (foreground, background, minimum): each pair the stylesheet actually draws.
PAIRS = [
    ("text", "bg", NORMAL_TEXT),
    ("text", "surface", NORMAL_TEXT),
    ("text", "soft", NORMAL_TEXT),
    ("muted", "bg", NORMAL_TEXT),
    ("muted", "surface", NORMAL_TEXT),
    ("accent", "surface", NORMAL_TEXT),
    ("accent", "bg", NORMAL_TEXT),
    ("accent-hover", "surface", NORMAL_TEXT),
    ("on-accent", "accent", NORMAL_TEXT),
    ("on-accent", "accent-hover", NORMAL_TEXT),
    ("on-danger", "danger-fill", NORMAL_TEXT),
    ("danger", "surface", NORMAL_TEXT),
    ("sidebar-text", "sidebar-bg", NORMAL_TEXT),
    ("sidebar-text", "sidebar-hover", NORMAL_TEXT),
    # The wordmark is a logotype, exempt from contrast rules (WCAG 1.4.3), so it is not listed.
    # Controls and focus indicators: non-text contrast.
    ("border-strong", "surface", LARGE_TEXT_OR_UI),
    ("focus", "bg", LARGE_TEXT_OR_UI),
    ("focus", "surface", LARGE_TEXT_OR_UI),
    ("sidebar-focus", "sidebar-bg", LARGE_TEXT_OR_UI),
    ("chart", "surface", LARGE_TEXT_OR_UI),
    *((f"slice-{index}", "surface", LARGE_TEXT_OR_UI) for index in range(8)),
]
FIXED = [
    # White labels on the active and hovered sidebar items, in both themes.
    ("#ffffff", "sidebar-active", NORMAL_TEXT),
    ("#ffffff", "sidebar-hover", NORMAL_TEXT),
    ("#9ca3af", "sidebar-bg", NORMAL_TEXT),
]


@pytest.mark.parametrize("theme", sorted(THEMES))
@pytest.mark.parametrize(("foreground", "background", "minimum"), PAIRS)
def test_token_pairs_meet_wcag_aa(
    theme: str, foreground: str, background: str, minimum: float
) -> None:
    tokens = THEMES[theme]

    ratio = contrast(tokens[foreground], tokens[background])

    assert ratio >= minimum, f"{theme}: {foreground} on {background} is {ratio:.2f}:1"


@pytest.mark.parametrize("theme", sorted(THEMES))
@pytest.mark.parametrize(("foreground", "background", "minimum"), FIXED)
def test_fixed_sidebar_colours_meet_wcag_aa(
    theme: str, foreground: str, background: str, minimum: float
) -> None:
    ratio = contrast(foreground, THEMES[theme][background])

    assert ratio >= minimum, f"{theme}: {foreground} on {background} is {ratio:.2f}:1"


def test_the_explicit_dark_theme_matches_the_system_dark_theme() -> None:
    explicit_start = CSS.index(':root[data-theme="dark"] {')
    explicit = _tokens(CSS[explicit_start : CSS.index("}", explicit_start)])
    media_start = CSS.index("@media (prefers-color-scheme: dark)")
    media = _tokens(CSS[media_start:explicit_start])

    assert explicit == media


def test_both_themes_define_the_same_tokens() -> None:
    assert set(THEMES["dark"]) == set(THEMES["light"])
