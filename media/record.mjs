#!/usr/bin/env node
/**
 * Scripted demo footage (issue #18).
 *
 * Drives presentation mode like a presenter would: opens the console, presses
 * P, holds each of the eight beats, advances with ArrowRight, and leaves with
 * Escape. The browser records the session; ffmpeg then makes the landscape
 * cut, the square social crop and the step-4 poster.
 *
 * Playwright's recordVideo ignores deviceScaleFactor, so the page itself is
 * zoomed to 1.5 over a 1920x1080 viewport. That keeps UI text phone-readable
 * while the video stays a true 1080p file.
 *
 * Usage:
 *   node record.mjs --base http://localhost:8013/ --out out/demo-v1
 */

import { execFile } from "node:child_process";
import { mkdtemp, mkdir, rm, stat } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import ffmpegPath from "ffmpeg-static";
import { chromium } from "playwright";

const exec = promisify(execFile);

/** Hold time per presentation beat, in seconds (the eight beats total ≈ 59 s). */
const STEP_SECONDS = [8, 7, 8, 9, 6, 8, 7, 6];

const VIEWPORT = { width: 1920, height: 1080 };
/** Playwright ignores deviceScaleFactor in recorded video; zoom the document instead. */
const PAGE_ZOOM = "1.5";
/** Step 4 is the spoof beat: the poster is taken there, before advancing. */
const POSTER_STEP = 4;
const SETTLE_MS = 2000;
const CRF = "20";
const LAUNCH_ARGS = ["--hide-scrollbars", "--enable-unsafe-swiftshader"];
const USAGE = "usage: node record.mjs --base http://localhost:8013/ --out out/demo-v1";

function parseArgs(argv) {
  const options = { base: "http://localhost:8013/", out: "out/demo-v1" };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const value = argv[index + 1];

    if (arg === "--help" || arg === "-h") {
      console.log(USAGE);
      process.exit(0);
    }
    if ((arg === "--base" || arg === "--out") && value !== undefined) {
      options[arg.slice(2)] = value;
      index += 1;
      continue;
    }
    throw new Error(`Unknown argument: ${arg}\n${USAGE}`);
  }

  return options;
}

/** `<base>?nointro#/`: skip the first-visit card and land on the root route. */
function startUrl(base) {
  const url = new URL(base);
  url.hash = "";
  url.searchParams.set("nointro", "1");
  return `${url.href}#/`;
}

/** Chrome if it is installed, the bundled Chromium otherwise. */
async function launchBrowser() {
  try {
    const browser = await chromium.launch({ channel: "chrome", args: LAUNCH_ARGS });
    return { browser, label: 'Chrome (channel "chrome")' };
  } catch {
    const browser = await chromium.launch({ args: LAUNCH_ARGS });
    return { browser, label: "bundled Chromium" };
  }
}

/** Re-assert the page scale after a navigation (and after each hash step). */
async function applyZoom(page) {
  await page.evaluate((zoom) => {
    document.documentElement.style.zoom = zoom;
  }, PAGE_ZOOM);
}

function displayPath(file) {
  const relative = path.relative(process.cwd(), file);
  return relative === "" || relative.startsWith("..") ? file : relative;
}

async function runFfmpeg(args) {
  try {
    await exec(ffmpegPath, args, { maxBuffer: 1 << 26 });
  } catch (error) {
    const detail = String(error?.stderr ?? "").trim().split("\n").slice(-8).join("\n");
    throw new Error(`ffmpeg failed:\n${detail}`);
  }
}

/** ffmpeg with no output exits 1 after printing the input's duration on stderr. */
async function durationSeconds(file) {
  try {
    await exec(ffmpegPath, ["-hide_banner", "-i", file], { maxBuffer: 1 << 20 });
    return null;
  } catch (error) {
    const text = `${error?.stderr ?? ""}${error?.stdout ?? ""}`;
    const match = /Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)/.exec(text);
    if (match === null) return null;
    return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]);
  }
}

async function record({ base, out }) {
  if (ffmpegPath === null) {
    throw new Error("ffmpeg-static has no binary for this platform.");
  }

  const start = startUrl(base);
  const outBase = path.resolve(out);
  const outDir = path.dirname(outBase);
  const mp4 = `${outBase}.mp4`;
  const square = `${outBase}-square.mp4`;
  const poster = `${outBase}-poster.png`;

  await mkdir(outDir, { recursive: true });
  const workDir = await mkdtemp(path.join(outDir, ".work-"));

  let browser = null;
  let context = null;
  let video = null;
  let webm = null;

  try {
    const launched = await launchBrowser();
    browser = launched.browser;
    console.log(`browser: ${launched.label}`);
    console.log(`open: ${start}`);

    context = await browser.newContext({
      viewport: VIEWPORT,
      deviceScaleFactor: 1,
      recordVideo: { dir: workDir, size: VIEWPORT },
    });
    const page = await context.newPage();
    video = page.video();

    await page.goto(start, { waitUntil: "domcontentloaded" });
    await applyZoom(page);
    await page.waitForLoadState("networkidle", { timeout: 60_000 }).catch(() => {
      console.warn("network did not fully idle; continuing after the settle pause");
    });
    await page.waitForTimeout(SETTLE_MS);

    await page.keyboard.press("p");
    await page.waitForSelector(".present-caption", { state: "visible", timeout: 10_000 });

    for (let step = 0; step < STEP_SECONDS.length; step += 1) {
      const index = step + 1;
      const seconds = STEP_SECONDS[step];
      console.log(`step ${index}/${STEP_SECONDS.length}: hold ${seconds}s`);
      await page.waitForTimeout(seconds * 1000);
      // Hash routing never reloads, but pinning the scale each beat is cheap.
      await applyZoom(page);

      if (index === POSTER_STEP) {
        await page.screenshot({ path: poster });
        console.log(`poster: ${displayPath(poster)}`);
      }

      if (step < STEP_SECONDS.length - 1) {
        await page.keyboard.press("ArrowRight");
      }
    }

    await page.keyboard.press("Escape");
    // Closing the context flushes the recorded video to disk.
    await context.close();
    context = null;
    webm = await video.path();
  } finally {
    await context?.close().catch(() => {});
    await browser?.close().catch(() => {});
  }

  const info = await stat(webm).catch(() => null);
  if (info === null || info.size === 0) {
    throw new Error("the browser did not write a video file");
  }

  const encode = (args) =>
    runFfmpeg([
      "-y",
      "-i",
      webm,
      "-an",
      "-c:v",
      "libx264",
      "-preset",
      "medium",
      "-crf",
      CRF,
      "-pix_fmt",
      "yuv420p",
      "-movflags",
      "+faststart",
      ...args,
    ]);

  let encoded = false;
  try {
    await encode([mp4]);
    await encode([
      "-vf",
      "crop=1080:1080:(iw-1080)/2:(ih-1080)/2",
      square,
    ]);
    encoded = true;
  } finally {
    // Keep the raw WebM only if encoding failed, so it can be retried by hand.
    if (encoded) {
      await rm(workDir, { recursive: true, force: true });
    } else {
      console.warn(`raw recording kept in ${displayPath(workDir)}`);
    }
  }

  const outputs = [
    [mp4, await durationSeconds(mp4)],
    [square, await durationSeconds(square)],
    [poster, null],
  ];

  for (const [file, seconds] of outputs) {
    const suffix = seconds === null ? "" : `  (${seconds.toFixed(1)}s)`;
    console.log(`${displayPath(file)}${suffix}`);
  }
}

record(parseArgs(process.argv.slice(2))).catch((error) => {
  console.error(`\n${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});
