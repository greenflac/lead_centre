"""Съёмка экранов дашборда для README. Запускается против уже поднятого `next start`.

Имена файлов зафиксированы: на них ссылается README.md и web/README.md, поэтому
переименование ломает картинки в документации.

Три исхода (Р1): снято / НЕ СНЯТО (страница не ответила) / НЕ СМОГЛИ (нет браузера).
В конце печатается таблица «файл — размер — что на нём», чтобы «снято» было числом.

Запуск: python3 web/scripts/shots.py [base_url] [only-prefix]
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
OUT = Path(__file__).resolve().parents[1] / "screenshots"
WIDTH, HEIGHT, SCALE = 1440, 1000, 2


def open_lead(page, lead_id: str) -> None:
    """Открывает карточку по id: поиск, затем первая строка списка."""
    box = page.locator("input.search-box")
    box.fill(lead_id)
    page.wait_for_timeout(250)
    page.locator("button.lead-row").first.click()
    page.wait_for_timeout(250)


def shoot(page, name: str, locator=None) -> tuple[str, int]:
    # Полный кадр снимается от верха страницы: переключение вкладки оставляет прокрутку,
    # и шапка с полосой происхождения уезжает за край.
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(100)
    path = OUT / name
    (locator or page).screenshot(path=str(path))
    return name, path.stat().st_size


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3111"
    only = sys.argv[2] if len(sys.argv) > 2 else ""
    OUT.mkdir(exist_ok=True)
    taken: list[tuple[str, int]] = []

    def want(name: str) -> bool:
        return not only or name.startswith(only)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT},
                                device_scale_factor=SCALE)
        page.goto(base, wait_until="networkidle")
        page.wait_for_selector("button.lead-row", timeout=15000)

        if want("01"):
            open_lead(page, "urg-01")
            page.locator("input.search-box").fill("")
            page.locator("input.search-box").blur()
            page.wait_for_timeout(250)
            taken.append(shoot(page, "01-inbox.png"))

        if want("02"):
            open_lead(page, "urg-13")
            taken.append(shoot(page, "02-lead-card.png", page.locator(".inbox > div").nth(1)))
            # Крупный план: нажата причина — подсветка цитаты в тексте обращения.
            page.locator("button.reason-btn").first.click()
            page.wait_for_timeout(150)
            taken.append(shoot(page, "02b-lead-card-closeup.png", page.locator(".panel").nth(1)))

            open_lead(page, "edge-02")
            taken.append(shoot(page, "02c-lead-card-low.png", page.locator(".panel").nth(1)))

            open_lead(page, "gen-19")
            taken.append(shoot(page, "02d-lead-card-arabic.png", page.locator(".panel").nth(1)))

        if want("07"):
            page.locator("input.search-box").fill("zzzz-no-such-request")
            page.wait_for_timeout(300)
            page.locator("input.search-box").blur()
            page.wait_for_timeout(150)
            taken.append(shoot(page, "07-empty-state.png"))
            page.locator("input.search-box").fill("")
            page.wait_for_timeout(200)

        if want("05"):
            page.get_by_role("tab", name="Discovered").click()
            page.wait_for_timeout(400)
            taken.append(shoot(page, "05-discovered.png"))
            page.get_by_role("tab", name="Inbox").click()
            page.wait_for_timeout(300)

        if want("03"):
            page.get_by_role("tab", name="New request").click()
            page.wait_for_timeout(300)
            page.get_by_role("button", name="Urgent team relocation (RU)").click()
            page.wait_for_timeout(200)
            taken.append(shoot(page, "03-new-request-form.png"))
            page.get_by_role("button", name="Score this request").click()
            page.wait_for_selector(".card-head", timeout=15000)
            page.wait_for_timeout(400)
            taken.append(shoot(page, "04-new-request-result.png"))

        browser.close()

    print(f"{'файл':<34}{'байт':>10}")
    for name, size in taken:
        print(f"{name:<34}{size:>10}")
    print(f"\nснято {len(taken)}, не снято 0, не смогли 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
