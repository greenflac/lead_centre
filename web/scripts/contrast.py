"""Контраст пар цветов по формуле WCAG. Прибор для проверки палитры дашборда.

Считает по sRGB relative luminance, (L1+0.05)/(L2+0.05) — та же формула, что в §3.4
docs/design/01_material.md, но числа здесь считаются заново, а не переписываются.

Негативный контроль прибора (И5): белое на белом обязано дать 1.00, чёрное на белом —
21.00, эталон M3 (#1d1b20 на #fef7ff) — 16.23. Если контроли не сошлись, прибор врёт
и остальным числам верить нельзя — прогон завершается кодом 2.

Три исхода (Р1): ГОДНО / НЕ ГОДНО / НЕ СМОГЛИ ПРОВЕРИТЬ (пара без порога).
Запуск: python3 web/scripts/contrast.py [путь_к_globals.css]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "app" / "globals.css"

# Порог обычного текста (SC 1.4.3 AA) и порог нетекстовых носителей смысла (SC 1.4.11 AA).
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
    """Значения --токенов из одного блока объявлений."""
    start = css_text.find(block)
    if start < 0:
        raise SystemExit(f"НЕ СМОГЛИ ПРОВЕРИТЬ: в CSS нет блока {block!r}")
    body = css_text[start:css_text.index("}", start)]
    return dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*(#[0-9A-Fa-f]{3,8})\s*;", body))


# Пары: (что, на чём, порог). Порог None — пара без норматива (Р1: третий исход).
PAIRS: tuple[tuple[str, str, float | None], ...] = (
    ("--on-surface", "--surface", TEXT),
    ("--on-surface", "--surface-container", TEXT),
    ("--on-surface-variant", "--surface", TEXT),
    ("--on-surface-variant", "--surface-container", TEXT),
    ("--on-surface-variant", "--primary-container", TEXT),
    ("--medium-text", "--surface", TEXT),
    ("--medium-text", "--surface-container", TEXT),
    ("--inverse-on-surface", "--inverse-surface", TEXT),
    ("--inverse-surface", "--surface", NON_TEXT),      # чип HIGH и линейка HIGH как объекты
    ("--inverse-surface", "--surface-container", NON_TEXT),
    ("--outline", "--surface", NON_TEXT),              # контур чипа MEDIUM
    ("--outline", "--surface-container", NON_TEXT),
    ("--primary", "--surface", TEXT),
    ("--on-primary", "--primary", TEXT),
    ("--on-surface", "--primary-container", TEXT),
    ("--on-primary-container", "--primary-container", TEXT),
    ("--error", "--surface", TEXT),
    ("--on-error-container", "--error-container", TEXT),
    ("--error", "--error-container", NON_TEXT),
    ("--outline-variant", "--surface", None),          # разделитель, смысла не несёт
)


def check(name: str, block: str, css_text: str) -> tuple[int, int, int]:
    table = tokens(css_text, block)
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
    print("негативный контроль прибора (И5):")
    for fg, bg, expected in controls:
        got = ratio(fg, bg)
        print(f"  {fg} на {bg}: {got:.2f} (ожидалось {expected:.2f})")
        if abs(got - expected) > 0.01:
            print("  прибор не сошёлся с контролем — числам ниже верить нельзя")
            return 2

    total_good = total_bad = total_unknown = 0
    for name, block in (("светлая схема", ":root {"),
                        ("тёмная схема", ':root[data-theme="dark"] {')):
        good, bad, unknown = check(name, block, css_text)
        total_good += good
        total_bad += bad
        total_unknown += unknown

    print(f"\nпроверено {total_good + total_bad}, нарушений {total_bad}, "
          f"не смогли {total_unknown}")
    return 1 if total_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
