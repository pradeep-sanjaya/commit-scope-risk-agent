import re
from pathlib import Path

from scope_agent.persistence.database import utcnow
from scope_agent.web import portal

CSS = (Path(portal.__file__).parent / "static" / "portal.css").read_text()
AA_PAIRS = [("fg", "bg"), ("fg", "panel"), ("muted", "panel"), ("accent", "panel"), ("high", "high-soft"),
            ("med", "med-soft"), ("low", "low-soft"), ("new", "new-soft"), ("fg", "ov"), ("on-accent", "accent"),
            ("muted", "low-soft"), ("accent", "accent-soft")]


def _tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;", block))


def _themes() -> tuple[dict, dict]:
    light = re.search(r":root\s*\{(.*?)\}", CSS, re.S)
    dark = re.search(r"prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{(.*?)\}", CSS, re.S)
    assert light and dark, "portal.css needs a :root token block and a dark-scheme :root block"
    return _tokens(light.group(1)), _tokens(dark.group(1))


def _luminance(hex_color: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_theme_tokens_meet_wcag_aa():
    for name, theme in zip(("light", "dark"), _themes()):
        for fg, bg in AA_PAIRS:
            assert fg in theme and bg in theme, f"{name}: --{fg}/--{bg} missing"
            ratio = contrast(theme[fg], theme[bg])
            assert ratio >= 4.5, f"{name}: --{fg} on --{bg} is {ratio:.2f}:1"


def test_stylesheet_is_self_contained():
    assert "@import" not in CSS
    assert not re.search(r"url\(\s*['\"]?https?:", CSS)
    assert "@font-face" not in CSS


def test_severity_macro_renders_badges():
    env = portal._environment(utcnow)
    html = env.from_string('{% from "portal/_macros.html.j2" import sev %}{{ sev("high") }}{{ sev("info") }}'
                           '{{ sev("medium", "2 med") }}').render()
    assert 'class="badge b-high">High<' in html
    assert 'class="badge b-low">Info<' in html
    assert 'class="badge b-medium">2 med<' in html


def test_ticket_keys_never_break_inside():
    """On narrow screens "DEMO-101" wrapped as "DEMO-" / "101"; keys may wrap between, not within."""
    rule = re.search(r"(?m)^\.key\s*\{([^}]*)\}", CSS)
    assert rule and "white-space: nowrap" in rule.group(1)
