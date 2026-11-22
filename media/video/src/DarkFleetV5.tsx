import React from "react";
import {
  AbsoluteFill,
  Audio,
  OffthreadVideo,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import plan from "../public/clips/v5plan_built.json";

/*
 * Dark Fleet demo v5, cut to the voice-over.
 *
 * Timing: composition time equals voice time (voice_master.wav starts at frame 0). Each voice line owns
 * one footage segment; the segment plays the matching source moment at a rate between 0.85x and 1.6x,
 * so the screen never freezes while the voice talks. Zooms are keyed in composition time and never
 * move the camera during a cut. A crossfade hides page loads only where the clip changes.
 */

export const V5_FPS = 30;

type Focus = { comp_t: number; scale: number; rect: number[] | null; target: string | null };
type Seg = { clip: string; src_start: number; src_end: number; comp_start: number; comp_end: number; rate: number; line: number };
type Line = { text: string; start: number; end: number; group?: string };
type Plan = { title_s: number; voice_total: number; end_s: number; segments: Seg[]; focus: Focus[]; lines: Line[] };
const P = plan as Plan;

const TITLE_F = Math.round(P.title_s * V5_FPS);
const VOICE_F = Math.round(P.voice_total * V5_FPS);
const END_F = Math.round(P.end_s * V5_FPS);
export const V5_TOTAL_SECONDS = P.voice_total + P.end_s;
export const V5_TOTAL_FRAMES = VOICE_F + END_F;

const XFADE_F = 9; // ~300 ms, only where the clip changes

// Window geometry, same as v4.
const WIN_W = 1640;
const SW = WIN_W / 1920;
const CONTENT_W = WIN_W;
const CONTENT_H = 1080 * SW;
const TITLE_BAR = 34;
const WIN_LEFT = (1920 - WIN_W) / 2;
const WIN_TOP = 64;

const FONT = "'IBM Plex Sans', 'Inter', Helvetica, Arial, sans-serif";

type Target = { s: number; cx: number; cy: number };
const FULL: Target = { s: 1, cx: 960, cy: 540 };

const KEYS = P.focus.map((f) => ({
  t: f.comp_t,
  target:
    f.rect && f.scale !== 1
      ? { s: f.scale, cx: f.rect[0] + f.rect[2] / 2, cy: f.rect[1] + f.rect[3] / 2 }
      : FULL,
}));

function lerpT(a: Target, b: Target, p: number): Target {
  return { s: a.s + (b.s - a.s) * p, cx: a.cx + (b.cx - a.cx) * p, cy: a.cy + (b.cy - a.cy) * p };
}

/** Camera position at composition frame `frame`: a ~0.7 s spring from each key to the next. */
function zoomAt(frame: number, fps: number): Target {
  let from = FULL;
  for (let i = 0; i < KEYS.length; i++) {
    const tk = Math.round(KEYS[i].t * fps);
    if (frame < tk) break;
    const nextT = i + 1 < KEYS.length ? Math.round(KEYS[i + 1].t * fps) : Infinity;
    const p = spring({
      frame: Math.min(frame, nextT - 1) - tk,
      fps,
      config: { damping: 18, stiffness: 120 },
    });
    const cur = lerpT(from, KEYS[i].target, p);
    if (frame < nextT) return cur;
    from = cur;
  }
  return from;
}

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

/** One voice line's footage. Plays source [src_start, ...] at `rate`; fades in only when the clip changes. */
const SegmentView: React.FC<{ seg: Seg; fadeIn: boolean }> = ({ seg, fadeIn }) => {
  const frame = useCurrentFrame();
  const opacity = fadeIn ? interpolate(frame, [0, XFADE_F], [0, 1], { extrapolateRight: "clamp" }) : 1;
  return (
    <AbsoluteFill style={{ opacity }}>
      <OffthreadVideo
        src={staticFile(`clips/${seg.clip}.webm`)}
        startFrom={Math.round(seg.src_start * V5_FPS)}
        playbackRate={seg.rate}
        muted
      />
    </AbsoluteFill>
  );
};

/** Subtitle: plain white text with a soft shadow, no box. Fades in and out within 4 frames (~130 ms). */
const Pill: React.FC<{ text: string; start: number; end: number }> = ({ text, start, end }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const f0 = Math.round(start * fps);
  const f1 = Math.round(end * fps);
  const fade = 4;
  const opacity = interpolate(frame, [f0, f0 + fade, f1 - fade, f1], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <div style={{ position: "absolute", left: 0, right: 0, bottom: 44, display: "flex", justifyContent: "center", opacity }}>
      <div
        style={{
          fontFamily: FONT,
          fontSize: 36,
          fontWeight: 600,
          lineHeight: 1.3,
          color: "#ffffff",
          textShadow: "0 2px 6px rgba(0,0,0,0.9), 0 0 2px rgba(0,0,0,0.85)",
          maxWidth: 1500,
          textAlign: "center",
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

const EndScene: React.FC = () => {
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
    </AbsoluteFill>
  );
};

export const DarkFleetV5: React.FC<{ captions: boolean; guide: boolean }> = ({ captions }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const z = zoomAt(frame, fps);

  // Clip change points get a crossfade: the outgoing segment runs XFADE_F frames longer underneath.
  const segs = P.segments;
  const clipChange = segs.map((s, i) => i > 0 && s.clip !== segs[i - 1].clip);

  return (
    <AbsoluteFill style={{ background: "#070b14" }}>
      <Background />
      <Window>
        <div style={stageStyle(z)}>
          {segs.map((s, i) => {
            const from = Math.round(s.comp_start * V5_FPS);
            const to = Math.round(s.comp_end * V5_FPS);
            const extra = i + 1 < segs.length && clipChange[i + 1] ? XFADE_F : 0;
            return (
              <Sequence key={i} from={from} durationInFrames={to - from + extra} layout="none">
                <SegmentView seg={s} fadeIn={clipChange[i]} />
              </Sequence>
            );
          })}
        </div>
      </Window>
      {P.lines
        .filter((l) => l.group !== undefined)
        .map((l) => (
          <ChapterLabel key={`g${l.start}`} text={l.group as string} start={l.start} />
        ))}
      {captions && P.lines.map((l) => <Pill key={`c${l.start}`} text={l.text} start={l.start} end={l.end} />)}
      <Sequence from={0} durationInFrames={TITLE_F} layout="none">
        <TitleScene />
      </Sequence>
      <Sequence from={VOICE_F} durationInFrames={END_F} layout="none">
        <EndScene />
      </Sequence>
      <Audio src={staticFile("voice_master.wav")} />
    </AbsoluteFill>
  );
};
