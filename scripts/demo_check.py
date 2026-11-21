#!/usr/bin/env python3
"""Pre-demo gate: drive the built app in Chrome and prove the Sunday snapshot reads clean.

Presentation mode is the one screen a judge watches, so this script walks the real
beats: it opens ``/?nointro#/`` at 1440x900, presses P, then advances with ArrowRight
until the caption stops changing (or after 12 beats). Each beat is screenshotted and
checked for the failure modes only a real run shows: an unfilled ``{token}``, an
``undefined``/``NaN``/``null`` leaking into the room, an empty caption, or a map
canvas with no size. After the walkthrough it loads every deep route once and fails
on any page or console error, because a shared judge link must not be a blank screen.

The Makefile's ``demo-check`` target builds the app and serves it from uvicorn; this
script only needs a base URL::

    uv run --no-project --with playwright python scripts/demo_check.py \\
        --base http://127.0.0.1:8099 --out out/demo-check

Exit code 0 means the demo is safe to give; 1 means at least one problem was found.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from playwright.async_api import Browser, ConsoleMessage, Page, async_playwright

VIEWPORT = {"width": 1440, "height": 900}

# The caption container built by present/present.ts; its text is what the room reads.
CAPTION = ".present-caption"

# Deep links the pitch or a judge may open directly. Every one must render clean.
ROUTES = (
    "#/",
    "#/hormuz",
    "#/live/BALTIC_WATCH",
    "#/live/MALAYSIA_EOPL",
    "#/live/BLACK_SEA",
    "#/live/BALTIC_WATCH/kind/spoof",
)

# Substrings that mean a caption was not filled from the snapshot.
BAD_CAPTION_TEXT = ("{", "}", "undefined", "NaN", "null")

STEP_PAUSE_MS = 2500
ROUTE_SETTLE_MS = 2000
MAX_STEPS = 12
CAPTION_FIRST_TIMEOUT_MS = 15_000

# Read the MapLibre canvas; null when no map is mounted at all.
CANVAS_BOX_JS = """
() => {
  const canvas =
    document.querySelector("canvas.maplibregl-canvas") ?? document.querySelector("canvas");
  if (canvas === null) return null;
  const box = canvas.getBoundingClientRect();
  return { width: box.width, height: box.height };
}
"""


def parse_args() -> argparse.Namespace:
    """--base is required (the running server); --out keeps step images together."""
    parser = argparse.ArgumentParser(description="Check the demo presentation against a live snapshot.")
    parser.add_argument(
        "--base", required=True, help="Base URL of the running app, e.g. http://127.0.0.1:8099"
    )
    parser.add_argument("--out", default="out/demo-check", help="Directory for step screenshots")
    return parser.parse_args()


def one_line(text: str) -> str:
    """Collapse the caption's newlines so each step prints as one line."""
    return " ".join(text.split())


class Recorder:
    """Collect page and console errors, labelled with the route being checked."""

    def __init__(self) -> None:
        self.context = "startup"
        self.problems: list[str] = []

    def attach(self, page: Page) -> None:
        page.on("pageerror", self._on_page_error)
        page.on("console", self._on_console)

    def _on_page_error(self, error: BaseException) -> None:
        self.problems.append(f"[{self.context}] page error: {error}")

    def _on_console(self, message: ConsoleMessage) -> None:
        # Warnings are noise (MapLibre logs a few); only errors fail the gate.
        if message.type == "error":
            self.problems.append(f"[{self.context}] console error: {message.text}")


async def check_canvas(page: Page, label: str, problems: list[str]) -> None:
    """Fail when no map is mounted or its canvas has been collapsed to zero size."""
    box = await page.evaluate(CANVAS_BOX_JS)
    if box is None:
        problems.append(f"{label}: no map canvas")
        return
    if box["width"] <= 0 or box["height"] <= 0:
        problems.append(f"{label}: map canvas has zero size ({box['width']}x{box['height']})")


def check_caption(caption: str, label: str, problems: list[str]) -> None:
    """Fail an empty caption or one that still carries a token or a null-ish value."""
    if caption == "":
        problems.append(f"{label}: empty caption")
        return
    for bad in BAD_CAPTION_TEXT:
        if bad in caption:
            problems.append(f"{label}: caption contains {bad!r}")


async def read_caption(page: Page) -> str:
    """The overlay's full text; missing element reads as empty so the step fails loudly."""
    locator = page.locator(CAPTION)
    if await locator.count() == 0:
        return ""
    return (await locator.first.inner_text()).strip()


async def walk_steps(page: Page, out: Path, recorder: Recorder) -> None:
    """Press P, then screenshot and check each beat until ArrowRight stops advancing."""
    await page.keyboard.press("p")
    try:
        await page.locator(CAPTION).first.wait_for(state="visible", timeout=CAPTION_FIRST_TIMEOUT_MS)
    except Exception:
        recorder.problems.append("presentation: caption never appeared after pressing P")
        return

    await page.wait_for_timeout(STEP_PAUSE_MS)

    previous = None
    for number in range(1, MAX_STEPS + 1):
        caption = await read_caption(page)

        # Reaching the last beat keeps the caption fixed; that is the stop signal,
        # so the repeated frame is never reported as another step.
        if caption == previous:
            break

        route = await page.evaluate("() => location.hash")
        label = f"step {number}"
        recorder.context = label

        before = len(recorder.problems)
        await page.screenshot(path=str(out / f"step-{number}.png"))
        check_caption(caption, label, recorder.problems)
        await check_canvas(page, label, recorder.problems)

        status = "OK" if len(recorder.problems) == before else "FAIL"
        print(f"step {number} {status} {route}: {one_line(caption)[:80]}")

        if number == MAX_STEPS:
            break
        previous = caption

        await page.keyboard.press("ArrowRight")
        await page.wait_for_timeout(STEP_PAUSE_MS)


async def check_routes(browser: Browser, base: str, recorder: Recorder) -> None:
    """Open every shareable route in a fresh page and fail on any new error."""
    for route in ROUTES:
        page = await browser.new_page(viewport=VIEWPORT)
        recorder.attach(page)
        recorder.context = route
        before = len(recorder.problems)
        await page.goto(f"{base}?nointro{route}", wait_until="load")
        await page.wait_for_timeout(ROUTE_SETTLE_MS)
        await page.close()
        state = "OK" if len(recorder.problems) == before else "FAIL"
        print(f"route {state} {route}")


async def run(base: str, out: Path) -> int:
    """Drive one Chrome session; return the process exit code."""
    out.mkdir(parents=True, exist_ok=True)
    recorder = Recorder()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome")
        try:
            page = await browser.new_page(viewport=VIEWPORT)
            recorder.attach(page)
            await page.goto(f"{base}?nointro#/", wait_until="load")
            await page.wait_for_timeout(ROUTE_SETTLE_MS)

            await walk_steps(page, out, recorder)

            # Leave presentation before the route sweep so its overlay cannot hide a screen,
            # and close the page so its live tail cannot report errors against another route.
            await page.keyboard.press("Escape")
            await page.close()

            await check_routes(browser, base, recorder)
        finally:
            await browser.close()

    if recorder.problems:
        print(f"DEMO CHECK FAIL: {len(recorder.problems)} problems", file=sys.stderr)
        for problem in recorder.problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print("DEMO CHECK PASS")
    return 0


def main() -> int:
    """Parse arguments and run the async gate."""
    args = parse_args()
    base = args.base.rstrip("/")
    try:
        return asyncio.run(run(base, Path(args.out)))
    except Exception as error:  # A missing Chrome or a dead server must fail the gate, not traceback.
        print(f"DEMO CHECK FAIL: 1 problems\n  demo-check error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
