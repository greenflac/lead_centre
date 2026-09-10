"""Ревизия системности CSS: сколько размеров шрифта, начертаний, отступов и радиусов.

Мерит ровно то, что §5.2 руководства 01_material.md измерил на прошлой версии
(10 размеров, 5 начертаний, 17 отступов из них 10 не кратны 4, 4 радиуса), чтобы
«стало лучше» было числом, а не словом.

Три исхода (Р1): ГОДНО / НЕ ГОДНО / НЕ СМОГЛИ ПРОВЕРИТЬ (файл не прочитан).
Запуск: python3 web/scripts/css_audit.py [css]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "app" / "globals.css"

ALLOWED_SIZES = {12, 14, 15, 16}      # 15px — только арабский блок (03_arabic_rtl §3.2)
ALLOWED_WEIGHTS = {400, 500}
GRID = {0, 4, 8, 12, 16, 20, 24, 32, 48}
ALLOWED_RADII = {"4px", "8px", "50%"}

SPACING_PROPS = ("padding", "margin", "gap", "row-gap", "column-gap")


def numbers(text: str, prop: str) -> list[tuple[str, str]]:
    """(значение, целая строка) для каждого объявления свойства prop и его вариантов."""
    pattern = re.compile(rf"(?<![-a-z]){prop}(?:-(?:top|right|bottom|left|inline|block)"
                         rf"(?:-(?:start|end))?)?\s*:\s*([^;{{}}]+);")
    return [(m.group(1).strip(), m.group(0)) for m in pattern.finditer(text)]


def px_values(raw: str) -> list[int]:
    out = []
    for token in raw.split():
        m = re.fullmatch(r"(-?\d+(?:\.\d+)?)px", token)
        if m:
            out.append(float(m.group(1)))
    return [abs(v) for v in out]


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else CSS
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"НЕ СМОГЛИ ПРОВЕРИТЬ: {exc}")
        return 2
    # Комментарии выкидываем: числа в пояснениях — не объявления.
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)

    sizes = sorted({float(v[:-2]) for v, _ in numbers(text, "font-size")
                    if v.endswith("px")})
    weights = sorted({int(v) for v, _ in numbers(text, "font-weight")
                      if v.isdigit()})
    spacing: set[float] = set()
    off_grid: list[str] = []
    for prop in SPACING_PROPS:
        for raw, line in numbers(text, prop):
            for value in px_values(raw):
                spacing.add(value)
                if value not in GRID:
                    off_grid.append(line.strip())
    radii = sorted({v for v, _ in numbers(text, "border-radius")
                    if not v.startswith("var(")})
    radius_vars = sorted(set(re.findall(r"--r-[a-z]+:\s*([^;]+);", text)))

    bad = 0
    print(f"размеров шрифта: {len(sizes)} -> {[int(s) if s == int(s) else s for s in sizes]}")
    if not set(sizes) <= {float(x) for x in ALLOWED_SIZES}:
        bad += 1
        print("  НЕ ГОДНО: есть размер вне шкалы 12/14/15/16")
    print(f"начертаний: {len(weights)} -> {weights}")
    if not set(weights) <= ALLOWED_WEIGHTS:
        bad += 1
        print("  НЕ ГОДНО: есть вес вне 400/500")
    print(f"значений отступа: {len(spacing)} -> {sorted(int(s) for s in spacing)}")
    print(f"  не кратных четырём: {len(off_grid)}")
    for line in sorted(set(off_grid)):
        bad += 1
        print(f"  НЕ ГОДНО: {line}")
    print(f"радиусы: токены {radius_vars}, литералы {radii}")
    for value in radii:
        if value not in ALLOWED_RADII:
            bad += 1
            print(f"  НЕ ГОДНО: радиус {value} вне набора 4px/8px")

    shadows = len(re.findall(r"box-shadow\s*:", text))
    print(f"теней (box-shadow): {shadows}")
    if shadows:
        bad += 1
    physical = sum(len(re.findall(rf"(?<![-a-z]){p}\s*:", text))
                   for p in ("border-left", "border-left-color", "border-right",
                             "padding-left", "margin-left"))
    physical += len(re.findall(r"text-align:\s*left", text))
    print(f"физических (не логических) свойств: {physical}")
    if physical:
        bad += 1

    print(f"\nпроверено 7 свойств системы, нарушений {bad}, не смогли 0")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
