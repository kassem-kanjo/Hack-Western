import React from "react";
import {
  AbsoluteFill,
  OffthreadVideo,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import timeline from "../public/clips/timeline.json";

/*
 * Dark Fleet demo v2, composed from the captured clips and the timeline.
 *
 * Captions are the lines of the v2 script, one per caption, timed at reading pace by the capture.
 * Chapter labels appear at the start of each group. Zoom moves the whole clip with a spring toward
 * each focus rectangle the capture logged; the capture only logs a zoom after a click has landed,
 * and a zoom out before the next cursor move.
 */

export const FPS = 30;

type Focus = { t: number; scale: number; rect: number[] | null; target: string | null };
type Line = { text: string; start: number; end: number; group?: string };
// A stretch of the clip replaced by another capture (a fresh page load), in clip seconds.
type Override = { clip: string; start: number; end: number; ready_s: number; dur: number; focus: Focus[] };
type Clip = { key: string; ready_s: number; dur: number; focus: Focus[]; lines: Line[]; override?: Override };
type Timeline = { title_s: number; end_s: number; clips: Clip[] };
const TL = timeline as Timeline;

const TITLE_S = TL.title_s;
const END_S = TL.end_s;
const END_LINE = "Dark Fleet. Built for sanctions teams and coast guards.";

// Window geometry (frame px): the clip at 0.854 scale with margins for the frame.
const WIN_W = 1640;
const SW = WIN_W / 1920;
const CONTENT_W = WIN_W;
const CONTENT_H = 1080 * SW;
const TITLE_BAR = 34;
const WIN_LEFT = (1920 - WIN_W) / 2;
const WIN_TOP = 64;

// Clip start times on the timeline (seconds).
const starts: number[] = [];
{
  let t = TITLE_S;
  for (const c of TL.clips) {
    starts.push(t);
    t += c.dur;
  }
}
const CLIPS_END_S = starts[TL.clips.length - 1] + TL.clips[TL.clips.length - 1].dur;
export const TOTAL_SECONDS = CLIPS_END_S + END_S;

const FONT = "'IBM Plex Sans', 'Inter', Helvetica, Arial, sans-serif";

type Target = { s: number; cx: number; cy: number };
const FULL: Target = { s: 1, cx: 960, cy: 540 };

function keysOf(clip: { focus: Focus[]; dur: number }): { t: number; target: Target }[] {
  const keys = clip.focus.map((f) => {
    const cx = f.rect ? f.rect[0] + f.rect[2] / 2 : 960;
    const cy = f.rect ? f.rect[1] + f.rect[3] / 2 : 540;
    return { t: f.t, target: { s: f.scale, cx, cy } };
  });
  // Ease back to the full view one second before the clip ends, so the cut is clean.
  const last = keys[keys.length - 1];
  if (last && last.target.s !== 1 && clip.dur - 1 > last.t) keys.push({ t: clip.dur - 1, target: FULL });
  return keys;
}

function lerpT(a: Target, b: Target, p: number): Target {
  return { s: a.s + (b.s - a.s) * p, cx: a.cx + (b.cx - a.cx) * p, cy: a.cy + (b.cy - a.cy) * p };
}

function zoomAt(keys: { t: number; target: Target }[], frame: number, fps: number): Target {
  let from = FULL;
  for (let i = 0; i < keys.length; i++) {
    const tk = Math.round(keys[i].t * fps);
    if (frame < tk) break;
    const nextT = i + 1 < keys.length ? Math.round(keys[i + 1].t * fps) : Infinity;
    const p = spring({ frame: Math.min(frame, nextT - 1) - tk, fps, config: { damping: 18, stiffness: 120 } });
    const cur = lerpT(from, keys[i].target, p);
    if (frame < nextT) return cur;
    from = cur;
  }
  return from;
}

/** Stage transform: the clip scaled by the window factor and the zoom, with the focus centred. */
function stageStyle(z: Target): React.CSSProperties {
  const S = z.s * SW;
  const tx = Math.min(0, Math.max(CONTENT_W - 1920 * S, CONTENT_W / 2 - z.cx * S));
  const ty = Math.min(0, Math.max(CONTENT_H - 1080 * S, CONTENT_H / 2 - z.cy * S));
  return {
    position: "absolute",
    left: 0,
    top: 0,
    width: 1920,
    height: 1080,
    transformOrigin: "0 0",
    transform: `translate(${tx}px, ${ty}px) scale(${S})`,
  };
}

const Background: React.FC = () => (
  <AbsoluteFill style={{ background: "linear-gradient(160deg, #070b14 0%, #0d1526 100%)" }}>
    <AbsoluteFill
      style={{
        backgroundImage:
          "linear-gradient(rgba(120,150,190,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(120,150,190,0.05) 1px, transparent 1px)",
        backgroundSize: "60px 60px",
      }}
    />
  </AbsoluteFill>
);

const Window: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div
    style={{
      position: "absolute",
      left: WIN_LEFT,
      top: WIN_TOP,
      width: WIN_W,
      height: TITLE_BAR + CONTENT_H,
      borderRadius: 18,
      overflow: "hidden",
      background: "#0b0f14",
      boxShadow: "0 40px 120px rgba(0,0,0,0.65), 0 0 0 1px rgba(255,255,255,0.06)",
    }}
  >
    <div
      style={{
        height: TITLE_BAR,
        background: "#151b26",
        display: "flex",
        alignItems: "center",
        padding: "0 14px",
        position: "relative",
        fontFamily: FONT,
        fontSize: 15,
        color: "#8a96a8",
      }}
    >
      {["#ff5f57", "#febc2e", "#28c840"].map((c) => (
        <span key={c} style={{ width: 12, height: 12, borderRadius: 6, background: c, marginRight: 8 }} />
      ))}
      <span style={{ position: "absolute", left: 0, right: 0, textAlign: "center" }}>Dark Fleet</span>
    </div>
    <div style={{ position: "relative", width: CONTENT_W, height: CONTENT_H, overflow: "hidden" }}>{children}</div>
  </div>
);

const Pill: React.FC<{ text: string; start: number; end: number }> = ({ text, start, end }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const f0 = Math.round(start * fps);
  const f1 = Math.round(end * fps);
  const fade = 6; // 0.2 s
  const opacity = interpolate(frame, [f0, f0 + fade, f1 - fade, f1], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const rise = spring({ frame: frame - f0, fps, config: { damping: 200 } });
  return (
    <div style={{ position: "absolute", left: 0, right: 0, bottom: 20, display: "flex", justifyContent: "center", opacity }}>
      <div
        style={{
          fontFamily: FONT,
          fontSize: 46,
          fontWeight: 600,
          lineHeight: 1.25,
          color: "#ffffff",
          background: "rgba(7,11,20,0.78)",
          padding: "12px 30px 14px",
          borderRadius: 16,
          maxWidth: 1500,
          textAlign: "center",
          transform: `translateY(${(1 - rise) * 14}px)`,
        }}
      >
        {text}
      </div>
    </div>
  );
};

const ChapterLabel: React.FC<{ text: string; start: number }> = ({ text, start }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const f0 = Math.round(start * fps);
  const opacity = interpolate(frame, [f0, f0 + 6, f0 + 0.8 * fps - 6, f0 + 0.8 * fps], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <div
      style={{
        position: "absolute",
        left: WIN_LEFT,
        top: 18,
        fontFamily: FONT,
        fontSize: 26,
        fontWeight: 600,
        color: "#c9d3e0",
        opacity,
      }}
    >
      {text}
    </div>
  );
};

const OverrideLayer: React.FC<{ o: Override }> = ({ o }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const z = zoomAt(keysOf(o), frame, fps);
  return (
    <AbsoluteFill style={{ background: "#0b0f14", overflow: "hidden" }}>
      <div style={stageStyle(z)}>
        <OffthreadVideo src={staticFile(`clips/${o.clip}.webm`)} startFrom={Math.round(o.ready_s * fps)} muted />
      </div>
    </AbsoluteFill>
  );
};

const ClipScene: React.FC<{ clip: Clip; captions: boolean }> = ({ clip, captions }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const z = zoomAt(keysOf(clip), frame, fps);
  return (
    <AbsoluteFill>
      <Background />
      <Window>
        <div style={stageStyle(z)}>
          <OffthreadVideo src={staticFile(`clips/${clip.key}.webm`)} startFrom={Math.round(clip.ready_s * fps)} muted />
        </div>
        {clip.override && (
          <Sequence
            from={Math.round(clip.override.start * fps)}
            durationInFrames={Math.round((clip.override.end - clip.override.start) * fps)}
          >
            <OverrideLayer o={clip.override} />
          </Sequence>
        )}
      </Window>
      {clip.lines
        .filter((l) => l.group !== undefined)
        .map((l) => (
          <ChapterLabel key={`g${l.start}`} text={l.group as string} start={l.start} />
        ))}
      {captions &&
        clip.lines.map((l) => <Pill key={`c${l.start}`} text={l.text} start={l.start} end={l.end} />)}
    </AbsoluteFill>
  );
};

const TitleScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = spring({ frame, fps, config: { damping: 200 } });
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", fontFamily: FONT }}>
      <Background />
      <div style={{ position: "relative", textAlign: "center", opacity: p, transform: `translateY(${(1 - p) * 20}px)` }}>
        <div style={{ fontSize: 124, fontWeight: 600, color: "#eef2f7", letterSpacing: -1 }}>Dark Fleet</div>
        <div style={{ marginTop: 22, fontSize: 40, color: "#9aa7b5", maxWidth: 1300, lineHeight: 1.3 }}>
          Ships switch off their transponders. We show where they could have gone, and who they could have met.
        </div>
      </div>
    </AbsoluteFill>
  );
};

const EndScene: React.FC<{ captions: boolean }> = ({ captions }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = spring({ frame, fps, config: { damping: 200 } });
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", fontFamily: FONT }}>
      <Background />
      <div style={{ position: "relative", textAlign: "center", opacity: p }}>
        <div style={{ fontSize: 110, fontWeight: 600, color: "#eef2f7" }}>Dark Fleet</div>
        <div style={{ marginTop: 20, fontSize: 44, color: "#9aa7b5" }}>github.com/kassem-kanjo/Hack-Western</div>
        <div style={{ marginTop: 12, fontSize: 34, color: "#6d7a8c" }}>Hack Western 13</div>
      </div>
      {captions && <Pill text={END_LINE} start={0.4} end={END_S} />}
    </AbsoluteFill>
  );
};

export const DarkFleet: React.FC<{ captions: boolean }> = ({ captions }) => (
  <AbsoluteFill style={{ background: "#070b14" }}>
    <Sequence from={0} durationInFrames={Math.round(TITLE_S * FPS)}>
      <TitleScene />
    </Sequence>
    {TL.clips.map((c, i) => (
      <Sequence key={c.key} from={Math.round(starts[i] * FPS)} durationInFrames={Math.round(c.dur * FPS)}>
        <ClipScene clip={c} captions={captions} />
      </Sequence>
    ))}
    <Sequence from={Math.round(CLIPS_END_S * FPS)} durationInFrames={Math.round(END_S * FPS)}>
      <EndScene captions={captions} />
    </Sequence>
  </AbsoluteFill>
);
