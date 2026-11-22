"""Record the demo video from the live app's Present mode (issue #18).

Opens `#/hormuz`, waits for live data, presses P, and steps through the Present
scenes with ArrowRight, holding each beat for its dwell time. Playwright records the
page as WebM; encoding to H.264 MP4 and GIF is a separate ffmpeg step (see
media/README.md).

Two cuts are written from the same live app:
  long  : all six scenes, about 55 s (Devpost cut)
  short : beat 1, the Watch live story and the Patrol unit, about 31 s

Beats are keyed by what the page shows: the step counter (`.present-step`), the
route hash and the scene's order inside its beat. Scenes the cut does not use are
passed over with a short keypress, so they do not hold on screen.

Safety: this script never clicks. It only presses keys and moves the pointer over the
Watch map. It never calls /api/edge/control*, and the Patrol Kill and Overload buttons
are never touched.

Run (from the repo root):
    ~/.local/bin/uv run --with playwright python scripts/record_demo.py \
        --base http://127.0.0.1:8000/ --cut long
    ~/.local/bin/uv run --with playwright python scripts/record_demo.py --cut short

Outputs go to media/out/demo-v2/ (gitignored by media/.gitignore):
    demo-v2-long.webm, demo-v2-short.webm, stills/*.png, manifest-<cut>.json
The manifest lists each beat, its caption text, the trim offset in the video
(seconds from the start of the WebM to the moment P was pressed) and every console
error and failed response, so the report can cite real numbers.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

VIEWPORT = {"width": 1280, "height": 720}
DEVICE_SCALE = 1.5

# Dwell per beat, in milliseconds. Beat keys match `beat_key()` below.
DWELL_MS = {
    "finding": 9000,
    "sea1": 8000,
    "cluster": 9000,
    "live": 12000,
    "patrol": 10000,
    "close": 5000,
}

CUTS: dict[str, list[str]] = {
    "long": ["finding", "sea1", "cluster", "live", "patrol", "close"],
    "short": ["finding", "live", "patrol"],
}

# Pause between skipped scenes. Short enough that a skipped card does not register.
SKIP_PAUSE_MS = 120
# Hold after the last caption before leaving Present mode.
TAIL_MS = 800
# Watch-map pointer sweep: how many steps, and the pause between steps (ms).
SWEEP_STEPS = 45
SWEEP_STEP_MS = 200


@dataclass
class Beat:
    key: str
    headline: str
    line: str
    route: str
    step: str
    dwell_ms: int
    t_start_s: float = 0.0  # seconds since P was pressed, measured on the host clock
    console_errors_before: int = 0


@dataclass
class Log:
    console_errors: list[str] = field(default_factory=list)
    failed_responses: list[str] = field(default_factory=list)


def beat_key(step: str, route_hash: str, ordinal_in_step: int) -> str:
    """Map what is on screen to a beat key. Unknown combinations return 'unknown'."""
    number = step.split("/")[0].strip()
    if route_hash.startswith("#/patrol"):
        return "patrol"
    if number == "1":
        return "finding"
    if number == "2":
        return "sea1" if ordinal_in_step == 0 else "cluster"
    if number == "3":
        return "live"
    if number == "5":
        return "close"
    return "unknown"


def wait_for_data(page: Page) -> None:
    """Wait until the first Present caption shows its live numbers and nothing is loading.

    Present mode fetches its numbers when it starts, so this runs after P is pressed.
    The first caption carries the Hormuz percentages (digits); until the fetch lands it
    shows the fallback sentence, which has none.
    """
    page.wait_for_selector(".present-headline", state="attached", timeout=30000)
    page.wait_for_function(
        """() => {
            const line = document.querySelector('.present-line');
            const head = document.querySelector('.present-headline');
            if (!line || !head) return false;
            if (line.textContent.includes('{') || head.textContent.includes('{')) return false;
            return /\\d/.test(line.textContent);
        }""",
        timeout=30000,
    )
    # Any loading state the screens show while the snapshot is fetched.
    page.wait_for_function(
        "() => document.querySelectorAll('.is-loading').length === 0",
        timeout=30000,
    )


def read_caption(page: Page) -> tuple[str, str, str]:
    head = page.locator(".present-headline").first.inner_text().strip()
    line = page.locator(".present-line").first.inner_text().strip()
    step = page.locator(".present-step").first.inner_text().strip()
    return head, line, step


def sweep_reach(page: Page, total_ms: int) -> None:
    """Move the pointer slowly across the Watch map's reach area. Never clicks."""
    box = page.locator(".watch-map").first.bounding_box()
    if box is None:
        # The map is not on screen; hold instead of guessing a position.
        page.wait_for_timeout(total_ms)
        return
    cx = box["x"] + box["width"] * 0.5
    cy = box["y"] + box["height"] * 0.5
    span = box["width"] * 0.22
    page.mouse.move(cx - span, cy)
    per_step = max(total_ms // SWEEP_STEPS, 1)
    for i in range(SWEEP_STEPS):
        t = i / (SWEEP_STEPS - 1)
        # Ease in and out: a slow, readable arc of the pointer across the reach.
        eased = 0.5 - 0.5 * math.cos(t * math.pi)
        x = cx - span + 2 * span * eased
        y = cy + span * 0.35 * math.sin(t * math.pi)
        page.mouse.move(x, y)
        page.wait_for_timeout(per_step)


def record(base: str, cut: str, out_dir: Path, headless: bool) -> dict:
    stills_dir = out_dir / "stills"
    stills_dir.mkdir(parents=True, exist_ok=True)
    video_dir = out_dir / "_raw"
    video_dir.mkdir(parents=True, exist_ok=True)
    log = Log()
    beats: list[Beat] = []
    wanted = CUTS[cut]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=DEVICE_SCALE,
            record_video_dir=str(video_dir),
            record_video_size=VIEWPORT,
        )
        # The WebM starts when the context is created; beat times are measured from here.
        t_ctx = time.monotonic()
        page = context.new_page()
        video = page.video
        page.on(
            "console",
            lambda m: log.console_errors.append(f"{m.type}: {m.text[:200]}") if m.type == "error" else None,
        )
        page.on("pageerror", lambda e: log.console_errors.append(f"pageerror: {str(e)[:200]}"))
        page.on(
            "response",
            lambda r: log.failed_responses.append(f"{r.status} {r.url[:160]}") if r.status >= 400 else None,
        )

        page.goto(f"{base.rstrip('/')}/?nointro#/hormuz", wait_until="load")
        page.wait_for_timeout(1500)

        page.keyboard.press("p")
        # Seconds into the WebM at the moment P was pressed: trim the video from here.
        trim_start_s = round(time.monotonic() - t_ctx, 2)
        page.wait_for_selector(".present-root", state="attached", timeout=10000)
        wait_for_data(page)
        page.wait_for_timeout(600)

        ordinal = 0
        last_step = ""
        seen_scenes = 0
        max_scenes = 12
        while seen_scenes < max_scenes:
            head, line, step = read_caption(page)
            route_hash = page.evaluate("location.hash")
            number = step.split("/")[0].strip()
            if number != last_step:
                ordinal = 0
                last_step = number
            key = beat_key(step, route_hash, ordinal)
            ordinal += 1
            seen_scenes += 1

            if key in wanted:
                dwell = DWELL_MS[key]
                beat = Beat(
                    key=key,
                    headline=head,
                    line=line,
                    route=route_hash,
                    step=step,
                    dwell_ms=dwell,
                    t_start_s=round(time.monotonic() - t_ctx, 2),
                    console_errors_before=len(log.console_errors),
                )
                beats.append(beat)
                page.wait_for_timeout(dwell // 2)
                page.screenshot(path=str(stills_dir / f"{cut}-{len(beats):02d}-{key}.png"))
                remaining = dwell - dwell // 2
                if key == "live":
                    sweep_reach(page, remaining)
                else:
                    page.wait_for_timeout(remaining)
            elif key == "unknown":
                # A scene this script does not know: hold it briefly and record it,
                # so the manifest shows what the app really showed.
                beats.append(
                    Beat(
                        key="unknown",
                        headline=head,
                        line=line,
                        route=route_hash,
                        step=step,
                        dwell_ms=0,
                        t_start_s=round(time.monotonic() - t_ctx, 2),
                        console_errors_before=len(log.console_errors),
                    )
                )
                page.wait_for_timeout(2000)
            else:
                page.wait_for_timeout(SKIP_PAUSE_MS)

            # The last scene leaves Present mode on ArrowRight; the last beat holds
            # its own dwell first, then Escape exits cleanly.
            if number == "5":
                break
            page.keyboard.press("ArrowRight")
            # Wait for the next caption to settle before reading it.
            page.wait_for_timeout(350)
            if page.locator(".present-root").count() == 0:
                break

        # Tail: hold the final frame briefly, then leave Present mode.
        page.wait_for_timeout(TAIL_MS)
        if page.locator(".present-root").count() > 0:
            page.keyboard.press("Escape")
        page.wait_for_timeout(800)

        context.close()
        raw_path = Path(video.path())
        browser.close()

    final = out_dir / f"demo-v2-{cut}.webm"
    if final.exists():
        final.unlink()
    shutil.move(str(raw_path), str(final))
    shutil.rmtree(video_dir, ignore_errors=True)

    manifest = {
        "cut": cut,
        "video": str(final),
        "viewport": VIEWPORT,
        "device_scale_factor": DEVICE_SCALE,
        "trim_start_s": trim_start_s,
        "beats": [b.__dict__ for b in beats],
        "console_error_count": len(log.console_errors),
        "console_errors": log.console_errors,
        "failed_response_count": len(log.failed_responses),
        "failed_responses": log.failed_responses,
    }
    (out_dir / f"manifest-{cut}.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base", default="http://127.0.0.1:8000/", help="Live app base URL")
    parser.add_argument("--cut", choices=sorted(CUTS), default="long")
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parents[1] / "media" / "out" / "demo-v2"),
        help="Output folder (gitignored media/out/ by default)",
    )
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    args = parser.parse_args()

    out_dir = Path(args.out).resolve()
    manifest = record(args.base, args.cut, out_dir, headless=not args.headed)
    keys = ("cut", "video", "console_error_count", "failed_response_count")
    print(json.dumps({k: manifest[k] for k in keys}, indent=2))
    for b in manifest["beats"]:
        print(f"{b['key']:8s} {b['step']:6s} {b['dwell_ms'] / 1000:>5.1f}s  {b['headline'][:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
