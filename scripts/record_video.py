# ruff: noqa: E501  (embedded page JS/CSS strings are kept on one line)
"""Capture the Dark Fleet demo clips (v2): real clicks, zooms motivated by what changed.

Captures only. Captions, zooms and the end card are composed in media/video (Remotion) from
the timeline written here. Each clip is one page session at 1920x1080 with CSS html zoom 1.5, so
the layout is 1280x720 at full frame size. Playwright records at the CSS viewport size and a
bounding box in CSS px is a frame pixel. The app is read-only here: no "Kill the AI", no
"Overload the Pi", no /api/edge/control*, the live camera frame is hidden on the Patrol page.

Motion rule: a zoom starts only after the click has landed and the UI has changed; a zoom
out is logged before the next cursor move.

Run (from the repo root):
    ~/.local/bin/uv run --with playwright python scripts/record_video.py
"""

from __future__ import annotations

import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

BASE = "http://127.0.0.1:8000/"
WORK_DIR = Path(
    "/private/tmp/claude-501/-Users-kassemkanjo--claude-spares-spare-uNpjIM/26a3a645-635a-422b-9ec0-3c65ba87508a/scratchpad/clips"
)
REPO_CLIPS = Path(__file__).resolve().parents[1] / "media" / "video" / "public" / "clips"
MAIN_SHIP = "GIVING"
LISTED = "STILGAR"
SATELLITE_SHIP = "BALTIC STAR"

WORDS_PER_S = 2.6
GAP_S = 0.4
TITLE_S = 4.0
END_S = 5.0

INIT_JS = r"""
(() => {
  if (window.__df) return;
  try { localStorage.setItem('df.intro.seen', '1'); } catch (e) {}
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  function setup() {
    if (window.__dfReady) return;
    window.__dfReady = true;
    document.documentElement.style.zoom = '1.5';
    const style = document.createElement('style');
    style.textContent = `
      html, body { overflow: hidden !important; background: #0b0f14; }
      .intro-backdrop { display: none !important; }
      img.patrol-frame-img, .patrol-frame canvas, .patrol-frame video { visibility: hidden !important; }
      .patrol-frame { background: #0b0f14 !important; }
      #df-cursor { position: fixed; left: 0; top: 0; width: 22px; height: 22px; margin: -11px 0 0 -11px;
        border-radius: 50%; pointer-events: none; z-index: 2147483647; opacity: 0;
        background: radial-gradient(circle, #ffffff 0 3.5px, rgba(255,255,255,.55) 4.5px 7px, rgba(255,255,255,0) 8px);
        box-shadow: 0 0 0 1.5px rgba(0,0,0,.45);
        transition: left 600ms cubic-bezier(.4,0,.2,1), top 600ms cubic-bezier(.4,0,.2,1), opacity 300ms, transform 140ms; }
      #df-cursor.press { transform: scale(.7); }
    `;
    document.head.appendChild(style);
    const cur = document.createElement('div');
    cur.id = 'df-cursor';
    document.body.appendChild(cur);
    const hideJunk = () => {
      document.querySelectorAll('button, a').forEach((b) => {
        if (/2-minute tour/i.test(b.textContent || '')) b.style.display = 'none';
      });
      document.querySelectorAll('div, p, span').forEach((el) => {
        if (el.children.length === 0 && /^New here\?/.test((el.textContent || '').trim())) {
          el.style.display = 'none';
          const p = el.parentElement;
          if (p && p.textContent.trim() === el.textContent.trim()) p.style.display = 'none';
        }
      });
      document.querySelectorAll('.watch-marks-key').forEach((k) => {
        k.style.display = 'none';
        if (k.parentElement) k.parentElement.style.display = 'none';
      });
    };
    setInterval(hideJunk, 300);
  }
  window.__df = {
    async moveTo(x, y) {
      const cur = document.getElementById('df-cursor');
      cur.style.opacity = '1';
      cur.style.left = x + 'px';
      cur.style.top = y + 'px';
      await sleep(650);
    },
    async press() {
      const cur = document.getElementById('df-cursor');
      cur.classList.add('press');
      await sleep(150);
      cur.classList.remove('press');
    },
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setup);
  else setup();
})();
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def line_dur(text: str) -> float:
    return round(len(text.split()) / WORDS_PER_S + GAP_S, 2)


def check_ships() -> None:
    """Stop before recording if the ships the script names are not live."""
    with urllib.request.urlopen(BASE + "api/watch", timeout=20) as resp:
        alerts = json.load(resp)["alerts"]
    names = {a.get("name") for a in alerts}
    missing = [n for n in (MAIN_SHIP, LISTED, SATELLITE_SHIP) if n not in names]
    if missing:
        raise SystemExit(f"not in /api/watch, tell Chief before recording: {missing}")
    log("ships live: " + ", ".join((MAIN_SHIP, LISTED, SATELLITE_SHIP)))


class Clip:
    """One page session. Each method is one step a viewer can follow."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.beat_start = 0.0
        self.ready = 0.0
        self.focus: list[dict] = []

    def now(self) -> float:
        return round(time.time() - self.beat_start, 3)

    def wait(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds))

    def wait_until(self, t: float) -> None:
        self.wait(t - self.now())

    def rect_of(self, target: str) -> list[float]:
        box = self.page.evaluate(
            """(t) => {
              if (t.startsWith('label:')) {
                const name = t.slice(6);
                const e = [...document.querySelectorAll('.watch-contact-label')].find((x) => (x.textContent || '').trim() === name);
                if (!e) return null;
                const r = e.getBoundingClientRect();
                return [r.x, r.y, r.width, r.height];
              }
              if (t.startsWith('union:')) {
                let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
                for (const sel of t.slice(6).split('|')) {
                  const e = document.querySelector(sel);
                  if (!e) return null;
                  e.scrollIntoView({ block: 'center' });
                  const r = e.getBoundingClientRect();
                  x0 = Math.min(x0, r.x); y0 = Math.min(y0, r.y);
                  x1 = Math.max(x1, r.right); y1 = Math.max(y1, r.bottom);
                }
                return [x0, y0, x1 - x0, y1 - y0];
              }
              let el = null;
              if (t.startsWith('text:')) {
                const p = t.slice(5);
                const c = [...document.querySelectorAll('body *')].filter((e) =>
                  e.getClientRects().length > 0 && (e.textContent || '').trim().startsWith(p));
                c.sort((a, b) => a.textContent.length - b.textContent.length);
                el = c[0] || null;
              } else { el = document.querySelector(t); }
              if (!el) return null;
              el.scrollIntoView({ block: 'center' });
              const r = el.getBoundingClientRect();
              return [r.x, r.y, r.width, r.height];
            }""",
            target,
        )
        if box is None:
            raise RuntimeError(f"focus target not found: {target}")
        return [round(v, 1) for v in box]

    def zoom_in(self, target: str, scale: float) -> None:
        """Log a zoom in on a target. Call only after the click has landed and the UI has changed."""
        self.focus.append({"t": self.now(), "scale": scale, "rect": self.rect_of(target), "target": target})

    def zoom_out(self) -> None:
        """Log the return to the full view. Call before the next cursor move."""
        self.focus.append({"t": self.now(), "scale": 1.0, "rect": None, "target": None})

    def click(self, selector: str, text: str | None = None) -> None:
        """Glide the cursor to an element (scrolled into view) and click it with a real mouse click."""
        pt = self.page.evaluate(
            """([sel, txt]) => {
              let els = [...document.querySelectorAll(sel)];
              if (txt !== null) els = els.filter((e) => (e.textContent || '').trim() === txt && e.getClientRects().length > 0);
              const e = els[0];
              if (!e) return null;
              e.scrollIntoView({ block: 'center' });
              const r = e.getBoundingClientRect();
              return [r.x + r.width / 2, r.y + r.height / 2];
            }""",
            [selector, text],
        )
        if pt is None:
            raise RuntimeError(f"click target not found: {selector} {text}")
        self.page.evaluate("([x, y]) => window.__df.moveTo(x, y)", pt)
        self.page.evaluate("() => window.__df.press()")
        self.page.mouse.click(pt[0], pt[1])


def run_lines(c: Clip, lines: list) -> tuple[float, list[dict]]:
    """Play the lines in order. Each action runs when its line starts. Timings are the real ones."""
    cum = 0.0
    timings: list[dict] = []
    for group, text, action in lines:
        c.wait_until(cum)
        start = c.now()
        if action is not None:
            action(c)
        entry = {"text": text, "start": round(start, 2), "end": round(start + line_dur(text) - GAP_S, 2)}
        if group is not None:
            entry["group"] = group
        timings.append(entry)
        cum += line_dur(text)
    c.wait_until(cum)
    return round(cum, 2), timings


def record_clip(p, key: str, url_hash: str, ready_selector: str, lines: list) -> dict:
    raw_dir = WORK_DIR / key
    shutil.rmtree(raw_dir, ignore_errors=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    browser = p.chromium.launch(channel="chrome")
    ctx = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        device_scale_factor=1,
        record_video_dir=str(raw_dir),
        record_video_size={"width": 1920, "height": 1080},
    )
    ctx.add_init_script(INIT_JS)
    t_page = time.time()
    page = ctx.new_page()
    clip = Clip(page)
    page.goto(BASE + url_hash, wait_until="domcontentloaded")
    page.wait_for_selector(ready_selector, state="attached", timeout=30000)
    clip.wait(2.5)
    clip.beat_start = time.time()
    clip.ready = round(clip.beat_start - t_page, 3)
    dur, timings = run_lines(clip, lines)
    video = page.video
    page.close()
    ctx.close()
    browser.close()
    dest = WORK_DIR / f"{key}.webm"
    shutil.move(str(Path(video.path())), dest)
    meta = {
        "key": key,
        "file": str(dest),
        "ready_s": clip.ready,
        "dur": dur,
        "focus": clip.focus,
        "lines": timings,
    }
    (WORK_DIR / f"{key}.json").write_text(json.dumps(meta, indent=2))
    log(f"{key}: ready {clip.ready:.2f}s, dur {dur:.1f}s, {len(timings)} lines, {len(clip.focus)} focus")
    return meta


def script_clips() -> list[tuple[str, str, str, list]]:
    """The v2 script (scratchpad script-v2.md). Each tuple: (clip key, start hash, ready selector, lines).

    Lines are (group label or None, caption text, action). Actions: click, then zoom in once the
    UI has changed; zoom out before the next cursor move.
    """

    def open_listed_label(c: Clip) -> None:
        # STILGAR's row in Happening now opens its own stop alert, which states the list match.
        c.click("span.watch-row-name", LISTED)
        c.wait(1.5)
        c.zoom_in("text:On a sanctions list: Yes", 1.6)

    def open_satellite(c: Clip) -> None:
        c.click("span.watch-row-name", SATELLITE_SHIP)
        c.wait(1.5)
        c.zoom_in(".satcheck", 1.5)

    def open_patrol_tab(c: Clip) -> None:
        c.click('a[href="#/patrol"]')
        c.page.wait_for_selector(".patrol-verdict", state="attached", timeout=30000)
        c.wait(1.0)

    def open_recorded(c: Clip) -> None:
        c.click(".patrol-recorded-btn")
        c.wait(1.0)
        c.zoom_in("union:.patrol-verdict|.patrol-big-number", 1.4)

    def click_ship(c: Clip) -> None:
        c.click("span.watch-row-name", MAIN_SHIP)
        c.wait(1.5)
        c.zoom_in(".maplibregl-canvas", 1.6)

    def zoom_out(c: Clip) -> None:
        c.zoom_out()

    def back_to_ship(c: Clip) -> None:
        # Zoom out first, then the cursor moves: back to GIVING's circle with both ships in view.
        c.zoom_out()
        c.wait(0.9)
        c.click("span.watch-row-name", MAIN_SHIP)
        c.wait(1.5)

    watch = [
        (
            "1 · The problem",
            "Every big ship broadcasts where it is, with a radio called a transponder.",
            None,
        ),
        (None, "Some switch it off, to hide where they go and who they meet.", None),
        (
            "2 · One ship goes dark",
            f"Last night in the Baltic, this ship, {MAIN_SHIP}, switched off its transponder.",
            click_ship,
        ),
        ("3 · Where it could be", "Since then, it could be anywhere inside this circle.", zoom_out),
        (
            "4 · Who it could have met",
            f"And inside that circle is {LISTED}, a ship on a sanctions list.",
            open_listed_label,
        ),
        (None, "It has been stopped at sea for hours.", None),
        (
            None,
            "Two ships, side by side, out of sight: that's how sanctioned oil moves from one hull to another.",
            None,
        ),
        (None, "We can't prove they met. We can show they could have, and where to look.", back_to_ship),
        ("5 · Where to look", "Satellite radar sees every ship, broadcasting or not.", open_satellite),
        (
            None,
            "One pass over the southern Baltic on Saturday found 7 ships that weren't broadcasting.",
            None,
        ),
    ]
    patrol = [
        (
            "6 · Eyes on the water",
            "On the water, our lookout runs on QNX, on a Raspberry Pi with a camera.",
            open_patrol_tab,
        ),
        (
            None,
            "It sees a vessel. Nothing nearby is broadcasting. It raises the alarm in under 200 milliseconds.",
            open_recorded,
        ),
        (None, "Under full load, 63 of 64 alarms still arrived on time.", zoom_out),
    ]
    return [
        ("watch", "#/watch", ".maplibregl-canvas", watch),
        ("patrol", "#/watch", ".maplibregl-canvas", patrol),
    ]


def record_climax() -> int:
    """Re-record the stretch between 'It has been stopped' and 'Satellite radar' as a fresh GIVING page.

    The watch clip's map goes black after the route change (bug #284), so this stretch is a separate
    clip. Its times come from the watch clip's caption lines, so the caption timings do not move.
    """
    tl_path = REPO_CLIPS / "timeline.json"
    tl = json.loads(tl_path.read_text())
    watch = next(c for c in tl["clips"] if c["key"] == "watch")
    lines = watch["lines"]

    def line_start(prefix: str) -> float:
        return next(ln["start"] for ln in lines if ln["text"].startswith(prefix))

    seg_start = line_start("It has been stopped")
    seg_end = line_start("Satellite radar")
    two_ships = line_start("Two ships") - seg_start
    cant_prove = line_start("We can't prove") - seg_start
    dur = round(seg_end - seg_start, 2)
    with urllib.request.urlopen(BASE + "api/watch", timeout=20) as resp:
        alerts = json.load(resp)["alerts"]
    main_id = next(a["id"] for a in alerts if a.get("kind") == "dark" and a.get("name") == MAIN_SHIP)
    raw_dir = WORK_DIR / "climax"
    shutil.rmtree(raw_dir, ignore_errors=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            device_scale_factor=1,
            record_video_dir=str(raw_dir),
            record_video_size={"width": 1920, "height": 1080},
        )
        ctx.add_init_script(INIT_JS)
        t_page = time.time()
        page = ctx.new_page()
        clip = Clip(page)
        page.goto(
            BASE + f"#/watch/alert/{main_id}", wait_until="load"
        )  # fresh load, not an in-app route change
        page.wait_for_selector(".maplibregl-canvas", state="attached", timeout=30000)
        clip.wait(3.5)  # let the map draw before the shot
        clip.beat_start = time.time()
        clip.ready = round(clip.beat_start - t_page, 3)
        clip.wait_until(two_ships)
        clip.zoom_in(f"label:{LISTED}", 1.6)  # push in on STILGAR's label, next to the GIVING track end
        clip.wait_until(cant_prove)
        clip.zoom_out()  # the whole circle, both ships in view
        clip.wait_until(dur)
        video_path = Path(page.video.path())  # read before the browser closes
        page.close()
        ctx.close()
        browser.close()
    dest = WORK_DIR / "climax.webm"
    shutil.move(str(video_path), dest)
    shutil.copy(dest, REPO_CLIPS / "climax.webm")
    watch["override"] = {
        "clip": "climax",
        "start": seg_start,
        "end": seg_end,
        "ready_s": clip.ready,
        "dur": dur,
        "focus": clip.focus,
    }
    tl_path.write_text(json.dumps(tl, indent=2))
    log(f"climax: {seg_start:.2f}-{seg_end:.2f} rel, ready {clip.ready:.2f}s, {len(clip.focus)} focus")
    return 0


def main() -> int:
    if sys.argv[1:] == ["climax"]:
        return record_climax()
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    REPO_CLIPS.mkdir(parents=True, exist_ok=True)
    check_ships()
    results = []
    with sync_playwright() as p:
        for key, url_hash, ready, lines in script_clips():
            results.append(record_clip(p, key, url_hash, ready, lines))
    timeline = {"title_s": TITLE_S, "end_s": END_S, "clips": []}
    for m in results:
        shutil.copy(m["file"], REPO_CLIPS / f"{m['key']}.webm")
        timeline["clips"].append(
            {
                "key": m["key"],
                "ready_s": m["ready_s"],
                "dur": m["dur"],
                "focus": m["focus"],
                "lines": m["lines"],
            }
        )
    (REPO_CLIPS / "timeline.json").write_text(json.dumps(timeline, indent=2))
    log(f"timeline written: {REPO_CLIPS / 'timeline.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
