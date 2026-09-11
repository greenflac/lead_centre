"""Shoots the dashboard screenshots the README links to, against a running `next start`.

File names are fixed: README.md and web/README.md reference them, so renaming breaks the
images in the docs.

Three outcomes: shot / not shot (the page did not answer) / could not (no browser).
Usage: python3 web/scripts/shots.py [base_url] [only-prefix]
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
OUT = Path(__file__).resolve().parents[1] / "screenshots"
WIDTH, HEIGHT, SCALE = 1440, 1000, 2


def open_lead(page, lead_id: str) -> None:
    """Opens a card by id: search, then the first row of the list."""
    box = page.locator("input.search-box")
    box.fill(lead_id)
    page.wait_for_timeout(250)
    page.locator("button.lead-row").first.click()
    page.wait_for_timeout(250)


def shoot(page, name: str, locator=None) -> tuple[str, int]:
    # A full frame is shot from the top: switching tabs keeps the scroll position and
    # pushes the header off the edge.
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

    def want_live(name: str) -> bool:
        """Live-mode frames are shot on explicit request only, never incidentally.

        Otherwise an ordinary run against the mock would shoot 08/09 with a "Live
        backend" label over demo data.
        """
        return bool(only) and name.startswith(only)

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
            # Close-up: a reason is pressed, highlighting its quote in the message.
            page.locator("button.reason-btn").first.click()
            page.wait_for_timeout(150)
            taken.append(shoot(page, "02b-lead-card-closeup.png", page.locator(".panel").nth(1)))

            open_lead(page, "edge-02")
            taken.append(shoot(page, "02c-lead-card-low.png", page.locator(".panel").nth(1)))

            open_lead(page, "gen-19")
            taken.append(shoot(page, "02d-lead-card-arabic.png", page.locator(".panel").nth(1)))

            # edge-03 is the longest request in the set: seven questions, five services.
            # The budget reason is pressed so the frame shows it backed by the client's
            # own sentence rather than a join of markers.
            open_lead(page, "edge-03")
            page.locator("button.reason-btn").nth(1).click()
            page.wait_for_timeout(150)
            taken.append(shoot(page, "02e-lead-card-long-request.png",
                               page.locator(".panel").nth(1)))

        if want("07"):
            # A plausible query rather than "zzzz": the empty state is shown to a manager,
            # so the search box holds something they might type. "Ras Al Khaimah" appears
            # in none of the 70 requests, so the state is genuinely empty.
            page.locator("input.search-box").fill("Ras Al Khaimah")
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

        # Live frames need a dashboard built with NEXT_PUBLIC_API_URL, since Next bakes
        # that variable into the build. `make shots-live` does the orchestration; this
        # script only shoots. Without the flag they are skipped on purpose: against the
        # mock the result is a "Live backend" label over demo data.
        if want_live("08"):
            taken.append(shoot(page, "08-live-backend.png"))

        if want_live("09"):
            page.get_by_role("tab", name="New request").click()
            page.wait_for_timeout(300)
            page.get_by_role("button", name="Urgent team relocation (RU)").click()
            page.wait_for_timeout(200)
            page.get_by_role("button", name="Score this request").click()
            # Live extraction takes seconds: wait for the card, not for a timer.
            page.wait_for_selector(".card-head", timeout=90000)
            page.wait_for_timeout(600)
            # The card itself is shot, not the viewport: on a live request it is taller
            # than the screen, and a viewport frame cut it off right before the draft.
            taken.append(shoot(page, "09-live-new-request.png",
                               page.locator(".panel").last))

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
