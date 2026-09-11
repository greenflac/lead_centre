"""WCAG contrast for the dashboard palette: (L1+0.05)/(L2+0.05) over sRGB luminance.

Known pairs act as the negative control -- white on white must give 1.00 and black on
white 21.00; if they do not, the numbers below mean nothing and the run exits with 2.

Three outcomes: pass / fail / could not check (a pair with no threshold).
Usage: python3 web/scripts/contrast.py [path to globals.css]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "app" / "globals.css"

# Thresholds: body text (SC 1.4.3 AA) and non-text carriers of meaning (SC 1.4.11 AA).
TEXT = 4.5
NON_TEXT = 3.0


def luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    channels = []
    for i in (0, 2, 4):
        c = int(value[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    lo, hi = sorted((a, b))
    return (hi + 0.05) / (lo + 0.05)


def tokens(css_text: str, block: str) -> dict[str, str]:
    """Returns the --token values declared in one block."""
    start = css_text.find(block)
    if start < 0:
        raise SystemExit(f"НЕ СМОГЛИ ПРОВЕРИТЬ: в CSS нет блока {block!r}")
    body = css_text[start:css_text.index("}", start)]
    return dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*(#[0-9A-Fa-f]{3,8})\s*;", body))


# Pairs: (foreground, background, threshold). None means no norm applies.
PAIRS: tuple[tuple[str, str, float | None], ...] = (
    ("--on-surface", "--surface", TEXT),
    ("--on-surface", "--surface-container", TEXT),
    ("--on-surface-variant", "--surface", TEXT),
    ("--on-surface-variant", "--surface-container", TEXT),
    ("--on-surface-variant", "--primary-container", TEXT),
    ("--medium-text", "--surface", TEXT),
    ("--medium-text", "--surface-container", TEXT),
    ("--inverse-on-surface", "--inverse-surface", TEXT),
    ("--inverse-surface", "--surface", NON_TEXT),      # HIGH chip and HIGH rule
    ("--inverse-surface", "--surface-container", NON_TEXT),
    ("--outline", "--surface", NON_TEXT),              # MEDIUM chip outline
    ("--outline", "--surface-container", NON_TEXT),
    ("--primary", "--surface", TEXT),
    ("--on-primary", "--primary", TEXT),
    ("--on-surface", "--primary-container", TEXT),
    ("--on-primary-container", "--primary-container", TEXT),
    ("--error", "--surface", TEXT),
    ("--on-error-container", "--error-container", TEXT),
    ("--error", "--error-container", NON_TEXT),
    ("--outline-variant", "--surface", None),          # a divider, carries no meaning
)


def check(name: str, block: str, css_text: str, prefix: str = "") -> tuple[int, int, int]:
    """Reads one palette; prefix selects it, since the dark tokens live as --dk-* in
    :root so their values are written exactly once."""
    raw = tokens(css_text, block)
    table = {("--" + key[len(prefix):]) if prefix and key.startswith(prefix) else key: value
             for key, value in raw.items()}
    if prefix:
        table = {key: value for key, value in table.items()
                 if not key.startswith("--dk-")}
    good = bad = unknown = 0
    print(f"\n=== {name} ({block}) ===")
    print(f"{'пара':<48}{'ratio':>8}{'порог':>8}  вердикт")
    for fg, bg, threshold in PAIRS:
        if fg not in table or bg not in table:
            print(f"{fg + ' / ' + bg:<48}{'—':>8}{'—':>8}  НЕ СМОГЛИ ПРОВЕРИТЬ (нет токена)")
            unknown += 1
            continue
        value = ratio(table[fg], table[bg])
        if threshold is None:
            print(f"{fg + ' / ' + bg:<48}{value:>8.2f}{'—':>8}  порога нет")
            unknown += 1
            continue
        ok = value >= threshold
        good, bad = (good + 1, bad) if ok else (good, bad + 1)
        print(f"{fg + ' / ' + bg:<48}{value:>8.2f}{threshold:>8.1f}  "
              f"{'годно' if ok else 'НЕ ГОДНО'}")
    return good, bad, unknown


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else CSS
    css_text = path.read_text(encoding="utf-8")

    controls = (("#ffffff", "#ffffff", 1.00), ("#000000", "#ffffff", 21.00),
                ("#1d1b20", "#fef7ff", 16.23))
    print("негативный контроль прибора:")
    for fg, bg, expected in controls:
        got = ratio(fg, bg)
        print(f"  {fg} на {bg}: {got:.2f} (ожидалось {expected:.2f})")
        if abs(got - expected) > 0.01:
            print("  прибор не сошёлся с контролем — числам ниже верить нельзя")
            return 2

    total_good = total_bad = total_unknown = 0
    for name, block, prefix in (("светлая схема", ":root {", ""),
                                ("тёмная схема (токены --dk-*)", ":root {", "--dk-")):
        good, bad, unknown = check(name, block, css_text, prefix)
        total_good += good
        total_bad += bad
        total_unknown += unknown

    print(f"\nпроверено {total_good + total_bad}, нарушений {total_bad}, "
          f"не смогли {total_unknown}")
    return 1 if total_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
