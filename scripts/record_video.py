# ruff: noqa: E501, E741, B905, F841  (one-off media capture script: embedded page JS/CSS, short loop names)
"""Capture the Dark Fleet demo v4: speech-timed captions, real clicks, numbers read off the screen.

Captures only. Each caption is one phrase of docs/script-v4 (scratchpad copy). Its duration is the
measured length of a macOS `say` (Samantha, 172 wpm) synthesis of that phrase, so the captions follow
natural speech. A phrase starts only after its action (click, UI change, zoom) has finished.

Numbers in the script's [brackets] are read from the live screen at capture time, never typed in.
The app is read-only here: no "Overload the Pi". "Kill the AI" is NOT clicked (Kassem's standing
rule for this demo); the QNX beat uses the recorded sighting only.

Writes, into media/video/public/clips: watch.webm, patrol.webm, timeline.json (clip timing, phrase
timing with audio paths), and the speech files stay in WORK_DIR/say.

Run (from the repo root):
    ~/.local/bin/uv run --with playwright python scripts/record_video.py
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

BASE = "http://127.0.0.1:8000/"
WORK_DIR = Path(
    "/private/tmp/claude-501/-Users-kassemkanjo--claude-spares-spare-uNpjIM/26a3a645-635a-422b-9ec0-3c65ba87508a/scratchpad/clips"
)
SAY_DIR = WORK_DIR / "say"
REPO_CLIPS = Path(__file__).resolve().parents[1] / "media" / "video" / "public" / "clips"
FFMPEG = "/Users/kassemkanjo/.cache/uv/archive-v0/RHoFjrEd9eng0E6Z/lib/python3.13/site-packages/imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v9.0"
MAIN_SHIP = "GIVING"
LISTED = "STILGAR"
SATELLITE_SHIP = "BALTIC STAR"
TITLE_S = 2.5

# Gaps after a phrase, in seconds: same sentence, end of sentence, beat change, reveal.
SAME = 0.25
SENT = 0.6
BEAT = 1.2
REVEAL = 1.5

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
        box-shadow: 0 0 0 1.5px rgba(0,0,0,.45); transition: opacity 300ms, transform 140ms; }
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
    /** Human-like cursor path: a curved quadratic path, ease in-out, speed varies per move. */
    async moveTo(x, y) {
      const cur = document.getElementById('df-cursor');
      if (cur.style.opacity !== '1') {
        cur.style.left = (x - 260 - Math.random() * 120) + 'px';
        cur.style.top = (y + 80 + Math.random() * 60) + 'px';
      }
      const sx = parseFloat(cur.style.left), sy = parseFloat(cur.style.top);
      const dist = Math.hypot(x - sx, y - sy);
      const dur = Math.min(1300, Math.max(450, dist * 1.2)) * (0.85 + Math.random() * 0.35);
      const bend = (Math.random() < 0.5 ? -1 : 1) * (0.08 + Math.random() * 0.1);
      const cx = (sx + x) / 2 - (y - sy) * bend, cy = (sy + y) / 2 + (x - sx) * bend;
      cur.style.opacity = '1';
      await new Promise((res) => {
        const t0 = performance.now();
        const step = (now) => {
          const u = Math.min(1, (now - t0) / dur);
          const e = u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2;
          cur.style.left = ((1 - e) * (1 - e) * sx + 2 * (1 - e) * e * cx + e * e * x) + 'px';
          cur.style.top = ((1 - e) * (1 - e) * sy + 2 * (1 - e) * e * cy + e * e * y) + 'px';
          if (u < 1) requestAnimationFrame(step); else res();
        };
        requestAnimationFrame(step);
      });
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


# ---- speech -------------------------------------------------------------------------------


def synth(text: str) -> tuple[Path, float]:
    """Synthesize one phrase with macOS say and return (file, measured duration in seconds)."""
    SAY_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(text.encode("utf-8")).hexdigest()[:14]
    aiff = SAY_DIR / f"{key}.aiff"
    if not aiff.exists():
        subprocess.run(["say", "-v", "Samantha", "-r", "172", "-o", str(aiff), text], check=True)
    err = subprocess.run([FFMPEG, "-i", str(aiff)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    if not m:
        raise RuntimeError(f"could not read duration of {aiff}")
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return aiff, round(dur, 3)


# ---- the page -----------------------------------------------------------------------------


class Clip:
    """One page session. Each method is one step a viewer can follow."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.beat_start = 0.0
        self.focus: list[dict] = []

    def now(self) -> float:
        return round(time.time() - self.beat_start, 3)

    def wait(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds))

    def wait_until(self, t: float) -> None:
        """Wait until this clip's clock reads t seconds (used to time actions to the voice)."""
        self.wait(t - self.now())

    def text_of(self, script: str) -> str:
        return self.page.evaluate(script)

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
        """Zoom in on a target. Call only after the UI change has landed."""
        self.focus.append({"t": self.now(), "scale": scale, "rect": self.rect_of(target), "target": target})

    def zoom_out(self) -> None:
        self.focus.append({"t": self.now(), "scale": 1.0, "rect": None, "target": None})

    def click(self, selector: str, text: str | None = None) -> None:
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


def run_speech(c: Clip, items: list[dict]) -> tuple[float, list[dict]]:
    """Speak the phrases in order. Each action runs first; the phrase starts after it has finished.

    A phrase's text may read numbers off the screen (a callable), so it is resolved after the action.
    """
    lines: list[dict] = []
    for it in items:
        if it.get("action") is not None:
            it["action"](c)
        text = it["text"](c) if callable(it["text"]) else it["text"]
        aiff, dur = synth(text)
        # Human pauses vary: each gap is its design value with a random 0.8-1.25 factor.
        gap = round(it["gap"] * random.uniform(0.8, 1.25), 2)
        entry = {"text": text, "start": round(c.now(), 2), "dur": dur, "gap_after": gap, "audio": str(aiff)}
        if it.get("group"):
            entry["group"] = it["group"]
        lines.append(entry)
        c.wait(dur + gap)
    # Subtitles: on screen for the speech plus a little, never under 1.2 s, never past the next phrase.
    for i, l in enumerate(lines):
        nxt = lines[i + 1]["start"] if i + 1 < len(lines) else l["start"] + l["dur"] + 1.2
        l["end"] = round(min(l["start"] + max(l["dur"] + 0.2, 1.2), nxt - 0.05), 2)
    return round(c.now(), 2), lines


def record(p, key: str, url_hash: str, ready: str, items: list[dict], tail: float = 0.8) -> dict:
    # Synthesize the fixed phrases before the clock starts, so no speech waits on `say`.
    for it in items:
        if isinstance(it["text"], str):
            synth(it["text"])
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
    page.wait_for_selector(ready, state="attached", timeout=30000)
    clip.wait(3.0)  # map and panel settle before the first shot
    clip.beat_start = time.time()
    ready_s = round(clip.beat_start - t_page, 3)
    dur, lines = run_speech(clip, items)
    clip.wait(tail)
    dur = round(clip.now(), 2)
    video_path = Path(page.video.path())
    page.close()
    ctx.close()
    browser.close()
    dest = WORK_DIR / f"{key}.webm"
    shutil.move(str(video_path), dest)
    log(f"{key}: ready {ready_s:.2f}s, dur {dur:.1f}s, {len(lines)} phrases, {len(clip.focus)} zooms")
    return {
        "key": key,
        "file": str(dest),
        "ready_s": ready_s,
        "dur": dur,
        "focus": clip.focus,
        "lines": lines,
    }


# ---- numbers read off the screen (never typed in) ---------------------------------------


def read_numbers(c: Clip, want: str) -> str:
    """Return a value from the live screen for a script bracket; raise if it is not there."""
    if want == "rate":
        body = c.text_of("() => document.body.innerText")
        m = re.search(r"(\d[\d,]*) position reports a minute", body)
        if not m:
            raise RuntimeError("live rate not on screen")
        return m.group(1)
    if want == "reach":
        note = c.text_of("() => (document.querySelector('.watch-reach-note') || {}).textContent || ''")
        m = re.search(
            r"(\d[\d,]*) ships were inside the circle; their own tracks rule out (\d[\d,]*)\. "
            r"Only (\d[\d,]*) could have been",
            note,
        )
        if not m:
            raise RuntimeError("reach note not on screen")
        return "|".join(g.replace(",", "") for g in m.groups())
    if want == "imo":
        s = c.text_of("() => (document.querySelector('.watch-sanctions.is-yes') || {}).textContent || ''")
        m = re.search(r"matched by IMO (\d+)", s)
        if not m:
            raise RuntimeError("IMO not on screen")
        return m.group(1)
    if want == "sat":
        s = c.text_of("() => (document.querySelector('.satcheck') || {}).textContent || ''")
        m = re.search(r"(\d+) h before it switched off", s)
        if not m:
            raise RuntimeError("satellite hours not on screen")
        return m.group(1)
    if want == "overload":
        s = c.text_of("() => (document.querySelector('.patrol-overload-line') || {}).textContent || ''")
        m = re.search(r"(\d+) of (\d+) alarms on time with our fix, (\d+) of (\d+) without", s)
        if not m:
            raise RuntimeError("overload numbers not on screen")
        return "|".join(m.groups())
    if want == "start":
        with urllib.request.urlopen(BASE + "api/watch", timeout=20) as resp:
            alerts = json.load(resp)["alerts"]
        a = next(a for a in alerts if a.get("name") == MAIN_SHIP and a.get("kind") == "dark")
        return a["t_start"][11:16]
    raise KeyError(want)


# ---- the script ---------------------------------------------------------------------------


def row_click(name: str, settle: float, after_zoom: tuple[str, float] | None = None):
    """Zoom out first, then the cursor moves, clicks the row, waits for the UI, then zooms in."""

    def act(c: Clip) -> None:
        c.zoom_out()
        c.wait(1.0)
        c.click("span.watch-row-name", name)
        c.wait(settle)
        if after_zoom:
            c.zoom_in(after_zoom[0], after_zoom[1])

    return act


def zoom(target: str, scale: float):
    return lambda c: c.zoom_in(target, scale)


def unzoom():
    return lambda c: c.zoom_out()


def watch_items() -> list[dict]:
    r = {}  # numbers, read once the page shows them (the lambdas below read them in order)

    def rate(c):
        return read_numbers(c, "rate")

    def reach_lookup(c):
        r["reach"] = read_numbers(c, "reach")
        return r["reach"]

    def inside_text(c):
        if "reach" not in r:
            reach_lookup(c)
        inside, ruled, could = r["reach"].split("|")
        if int(ruled) * 2 <= int(inside):
            raise RuntimeError("'most of them' is not true on screen")
        return f"{int(inside)} ships were inside the circle. Their tracks rule out most of them."

    def could_text(c):
        if "reach" not in r:
            reach_lookup(c)
        return f"{int(r['reach'].split('|')[2])} could have been in the same place, at the same time."

    def start_text(c):
        return f"This one, GIVING, went quiet at {read_numbers(c, 'start')} UTC."

    def imo_text(c):
        read_numbers(c, "imo")  # the sanctions line with the IMO must be on screen when this is said
        return "We match on the IMO number, because a name is easy to change."

    def sat_text(c):
        return f"This pass was {read_numbers(c, 'sat')} hours before the switch-off, and the app says so."

    def rate_text(c):
        return f"About {rate(c)} position reports a minute, streaming into a Tiger Data hypertable."

    def open_listed(c):
        c.zoom_out()
        c.wait(1.0)
        c.click("span.watch-row-name", LISTED)
        c.wait(1.5)
        c.zoom_in("text:On a sanctions list: Yes", 1.6)

    def back_to_ship(c):
        c.zoom_out()
        c.wait(1.0)
        c.click("span.watch-row-name", MAIN_SHIP)
        c.wait(1.8)

    def open_satellite(c):
        c.zoom_out()
        c.wait(1.0)
        c.click("span.watch-row-name", SATELLITE_SHIP)
        c.wait(2.5)  # let the map draw before the panel is read

    return [
        {"group": "1 · Live", "text": "This is Dark Fleet, running live.", "gap": SENT, "action": None},
        {"text": "Every dot is a ship we're hearing in the Baltic right now.", "gap": SENT, "action": None},
        {"text": rate_text, "gap": BEAT, "action": zoom("text:Live ·", 1.4)},
        {
            "group": "2 · A ship goes dark",
            "text": "On the left: ships that just switched off their transponders, the radio that says where they are.",
            "gap": SENT,
            "action": row_click(MAIN_SHIP, 2.0),
        },
        {"text": start_text, "gap": BEAT, "action": zoom(".maplibregl-canvas", 1.6)},
        {
            "group": "3 · The circle is physics",
            "text": "The circle isn't a guess.",
            "gap": SENT,
            "action": unzoom(),
        },
        {
            "text": "It's the top speed for its class, times the time it's been silent.",
            "gap": SENT,
            "action": None,
        },
        {
            "text": "And we only count it if our receivers were hearing that area at the time.",
            "gap": SENT,
            "action": None,
        },
        {"text": "Otherwise it's a coverage gap, not a ship hiding.", "gap": BEAT, "action": None},
        {
            "group": "4 · Who it could have met",
            "text": "Then we check every other ship's own track.",
            "gap": SENT,
            "action": zoom(".watch-reach-note", 1.5),
        },
        {"text": inside_text, "gap": SENT, "action": None},
        {"text": could_text, "gap": BEAT, "action": None},
        {
            "group": "5 · The match",
            "text": "One of them is STILGAR. It's on a sanctions list.",
            "gap": SENT,
            "action": open_listed,
        },
        {"text": imo_text, "gap": REVEAL, "action": None},
        {"text": "Two ships, out of sight, side by side.", "gap": SAME, "action": back_to_ship},
        {"text": "That's how sanctioned oil changes hands.", "gap": SENT, "action": None},
        {"text": "We can't prove they met. We show where to look.", "gap": BEAT, "action": None},
        {"group": "6 · From space", "text": "So we look from space.", "gap": SENT, "action": open_satellite},
        {
            "text": "We pull the latest Sentinel-1 radar pass and run our own ship detector on it.",
            "gap": SENT,
            "action": None,
        },
        {
            "text": "It flags anything brighter than the sea around it, then matches each blip to a broadcast.",
            "gap": SENT,
            "action": None,
        },
        {
            "text": "Red means radar sees a ship that isn't broadcasting.",
            "gap": SENT,
            "action": zoom(".satcheck-chips", 1.6),
        },
        {"text": sat_text, "gap": BEAT, "action": None},
    ]


def patrol_items() -> list[dict]:
    r = {}

    def overload_text(c):
        r["ov"] = read_numbers(c, "overload")
        a, b, cc, d = r["ov"].split("|")
        return f"Under full CPU overload, {a} of {b} alarms still landed on time."

    def before_text(c):
        if "ov" not in r:
            overload_text(c)
        a, b, cc, d = r["ov"].split("|")
        return f"Before we fixed the priorities: {cc} of {d}."

    def open_patrol(c):
        c.zoom_out()
        c.wait(1.0)
        c.click('a[href="#/patrol"]')
        try:
            c.page.wait_for_selector(".patrol-verdict", state="attached", timeout=6000)
        except Exception:  # the click missed the tab: change the route, the page is the same
            c.page.evaluate("h => { location.hash = h; }", "#/patrol")
            c.page.wait_for_selector(".patrol-verdict", state="attached", timeout=30000)
        c.wait(1.0)

    def open_recorded(c):
        c.page.wait_for_selector(".patrol-recorded-btn", state="attached", timeout=15000)
        c.click(".patrol-recorded-btn")
        c.wait(1.0)
        c.zoom_in("union:.patrol-verdict|.patrol-big-number", 1.4)

    def overload_zoom(c):
        c.zoom_in(".patrol-overload-line", 1.6)

    return [
        {
            "group": "7 · On the water",
            "text": "And on the water: a Raspberry Pi running QNX, with a camera.",
            "gap": SAME,
            "action": open_patrol,
        },
        {"text": "It's on our table.", "gap": SENT, "action": None},
        {"text": "It sees a vessel, nothing nearby is broadcasting.", "gap": SAME, "action": open_recorded},
        {"text": "It's inside a 200 ms deadline.", "gap": SENT, "action": None},
        {
            "text": "The safety loop runs at QNX's top fixed priority. The AI runs below it, untrusted.",
            "gap": BEAT,
            "action": unzoom(),
        },
        {"text": overload_text, "gap": SAME, "action": overload_zoom},
        {"text": before_text, "gap": BEAT, "action": None},
        {
            "group": "8 · Close",
            "text": "Live tracking, physics, satellite radar and a real-time lookout, built in 36 hours at Hack Western 13.",
            "gap": SENT,
            "action": unzoom(),
        },
        {"text": "That's Dark Fleet.", "gap": REVEAL, "action": None},
    ]


def main() -> int:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    REPO_CLIPS.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(BASE + "api/watch", timeout=20) as resp:
        names = {a.get("name") for a in json.load(resp)["alerts"]}
    missing = [n for n in (MAIN_SHIP, LISTED, SATELLITE_SHIP) if n not in names]
    if missing:
        raise SystemExit(f"not live, tell Chief before recording: {missing}")
    with sync_playwright() as p:
        watch = record(p, "watch", "#/watch/alert/" + pick_main_id(), ".maplibregl-canvas", watch_items())
        patrol = record(p, "patrol", "#/watch", ".maplibregl-canvas", patrol_items())
    timeline = {"title_s": TITLE_S, "end_s": 5.0, "clips": []}
    for m in (watch, patrol):
        shutil.copy(m["file"], REPO_CLIPS / f"{m['key']}.webm")
        timeline["clips"].append({k: m[k] for k in ("key", "ready_s", "dur", "focus", "lines")})
    (REPO_CLIPS / "timeline.json").write_text(json.dumps(timeline, indent=2))
    log(f"timeline written: {REPO_CLIPS / 'timeline.json'}")
    return 0


def pick_main_id() -> str:
    with urllib.request.urlopen(BASE + "api/watch", timeout=20) as resp:
        alerts = json.load(resp)["alerts"]
    return next(a["id"] for a in alerts if a.get("name") == MAIN_SHIP and a.get("kind") == "dark")


# ---- v5: the same actions, timed to the voice track -------------------------------------

VOICE = json.loads(
    Path(
        "/private/tmp/claude-501/-Users-kassemkanjo--claude-spares-spare-uNpjIM/26a3a645-635a-422b-9ec0-3c65ba87508a/scratchpad/vo/work/voice_lines.json"
    ).read_text()
)
WATCH_BASE = 2.6  # the watch clip starts when the voice does (title card is 0-2.6 s)


def run_slots(c: Clip, items: list[dict], starts: list[float], base: float, end: float) -> list[dict]:
    """Play each phrase's action at the voice time of its line. No synthesis: the voice is the friends' track."""
    out = []
    for it, vs in zip(items, starts):
        c.wait_until(vs - base)
        start = c.now()
        if it.get("action") is not None:
            it["action"](c)
        # The caption is the script wording; numbers come from the screen at this moment.
        text = it["text"](c) if callable(it["text"]) else it["text"]
        out.append({"text": text, "start": round(start, 2)})
        if it.get("group"):
            out[-1]["group"] = it["group"]
    c.wait_until(end - base)
    return out


def record_slots(
    p, key: str, url_hash: str, ready: str, items: list[dict], starts: list[float], base: float, end: float
) -> dict:
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
    page.wait_for_selector(ready, state="attached", timeout=30000)
    clip.wait(3.0)
    clip.beat_start = time.time()
    ready_s = round(clip.beat_start - t_page, 3)
    lines = run_slots(clip, items, starts, base, end)
    dur = round(clip.now(), 2)
    video_path = Path(page.video.path())
    page.close()
    ctx.close()
    browser.close()
    dest = WORK_DIR / f"{key}-v5.webm"
    shutil.move(str(video_path), dest)
    log(f"{key}: ready {ready_s:.2f}s, dur {dur:.2f}s, {len(lines)} phrases, {len(clip.focus)} zooms")
    return {
        "key": key,
        "file": str(dest),
        "ready_s": ready_s,
        "dur": dur,
        "focus": clip.focus,
        "lines": lines,
    }


def main_v5() -> int:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    REPO_CLIPS.mkdir(parents=True, exist_ok=True)
    vl = VOICE["lines"]
    starts = [l["start"] for l in vl]
    total = VOICE["total"]
    split = next(i for i, l in enumerate(vl) if l["old_cap_start"] >= 111.56)  # first patrol line
    script = (
        json.loads(Path(WORK_DIR / "v5-items.json").read_text())
        if (WORK_DIR / "v5-items.json").exists()
        else None
    )
    watch_its = watch_items()
    patrol_its = patrol_items()
    assert len(watch_its) == split and len(patrol_its) == len(vl) - split, (
        len(watch_its),
        split,
        len(patrol_its),
    )
    with sync_playwright() as p:
        w = record_slots(
            p,
            "watch",
            "#/watch/alert/" + pick_main_id(),
            ".maplibregl-canvas",
            watch_its,
            starts[:split],
            WATCH_BASE,
            starts[split],
        )
        q = record_slots(
            p, "patrol", "#/watch", ".maplibregl-canvas", patrol_its, starts[split:], starts[split], total
        )
    base_of = {"watch": WATCH_BASE, "patrol": starts[split]}
    timeline = {"title_s": 2.6, "end_s": 4.0, "voice_total": total, "clips": []}
    for m, key in ((w, "watch"), (q, "patrol")):
        shutil.copy(m["file"], REPO_CLIPS / f"{key}.webm")
        b = base_of[key]
        lines_out = []
        for l, v in zip(m["lines"], vl[split:] if key == "patrol" else vl[:split]):
            entry = {"text": l["text"], "start": round(v["start"] - b, 2), "end": round(v["end"] - b, 2)}
            if l.get("group"):
                entry["group"] = l["group"]
            lines_out.append(entry)
        timeline["clips"].append(
            {
                "key": key,
                "ready_s": m["ready_s"],
                "dur": round((total if key == "patrol" else starts[split]) - b, 2),
                "focus": m["focus"],
                "lines": lines_out,
            }
        )
    (REPO_CLIPS / "timeline-v5.json").write_text(json.dumps(timeline, indent=2))
    log("timeline-v5 written")
    return 0


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "v5":
    sys.exit(main_v5())
