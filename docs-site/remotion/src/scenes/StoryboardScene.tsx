import React from "react";
import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { WindowFrame } from "../components/WindowFrame";
import { Sidebar } from "../components/Sidebar";
import { PageHeader, CONTENT_X, CONTENT_Y } from "../components/PageHeader";
import { ImButton } from "../components/ImButton";
import { AnimatedCursor } from "../components/AnimatedCursor";
import { SHOTS, RECUT_SHOTS, CUT_FILM_SECONDS, RECUT_FILM_SECONDS, THESIS, type Shot } from "../fixture";

// The same public library and saved cuts as the browser tests. No model proposals
// are invented for the demo: this fixture records the final choice and its reason.
export { SHOTS, THESIS };
export type { Shot };
const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const meta: React.CSSProperties = { display: "flex", justifyContent: "space-between", fontSize: 11, color: COLORS.textSecondary, fontVariantNumeric: "tabular-nums" };

const ShotTile: React.FC<{ shot: Shot; index: number; active: boolean }> = ({ shot, index, active }) => (
  <div style={{ padding: 7, border: `2px solid ${active ? COLORS.primary : "transparent"}`, borderRadius: 8 }}>
    <div style={meta}><span>{String(index + 1).padStart(2, "0")}</span><span>{clock(shot.start)}</span></div>
    <Img src={staticFile(shot.picture)} style={{ display: "block", width: "100%", height: 150, objectFit: "contain", background: COLORS.surface, borderRadius: 4, margin: "6px 0" }} />
    <div style={meta}><span>{shot.day}</span><span>{shot.seconds.toFixed(1)} s</span></div>
    <div style={{ fontSize: 13, fontWeight: 600, marginTop: 6, lineHeight: 1.4 }}>{shot.story}</div>
    <div style={{ fontSize: 11, color: COLORS.textSecondary, marginTop: 6 }}>{shot.motion ? "Video" : "Still"}</div>
  </div>
);

const Inspector: React.FC<{ shot: Shot }> = ({ shot }) => (
  <div style={{ border: `1px solid ${COLORS.border}`, borderRadius: 10, background: COLORS.surface, overflow: "hidden" }}>
    <Img src={staticFile(shot.picture)} style={{ width: "100%", height: 220, objectFit: "contain", background: COLORS.bg }} />
    <div style={{ padding: 18 }}>
      <div style={meta}><span>{shot.day}</span><span>{clock(shot.start)} / {shot.seconds.toFixed(1)} s</span></div>
      <div style={{ fontSize: 17, fontWeight: 600, lineHeight: 1.4, margin: "10px 0 20px" }}>{shot.story}</div>
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Why this picture</div>
      <div style={{ fontSize: 13, lineHeight: 1.6 }}>{shot.reason}</div>
      <div style={{ borderTop: `1px solid ${COLORS.border}`, marginTop: 20, paddingTop: 18, fontSize: 13 }}>
        <div style={{ fontWeight: 600, display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ background: COLORS.primary, color: COLORS.bg, width: 17, height: 17, borderRadius: 2, textAlign: "center" }}>✓</span>Include in export
        </div>
        <div style={{ fontSize: 11, color: COLORS.textSecondary, lineHeight: 1.6, margin: "10px 0" }}>Export uses your selection. Cut again replans it.</div>
        {[...(shot.motion ? ["Trim the video clips"] : []), "Picture decisions", "Find alternatives in the pool"].map(label => (
          <div key={label} style={{ border: `1px solid ${COLORS.border}`, borderRadius: 6, padding: "8px 10px", fontSize: 12, marginTop: 10, background: COLORS.elevated }}>{label}</div>
        ))}
      </div>
    </div>
  </div>
);

type Props = { bassIntensity?: number; without?: number[]; frames: number; clickTarget: "pool" | "export"; clickAt: number };

export const StoryboardScene: React.FC<Props> = ({ bassIntensity, without = [], frames, clickTarget, clickAt }) => {
  const frame = useCurrentFrame();
  const shots = without.length ? RECUT_SHOTS : SHOTS;
  const film = without.length ? RECUT_FILM_SECONDS : CUT_FILM_SECONDS;
  const selected = frame < 48 || without.length ? 0 : 3;
  const scroll = clickTarget === "export" ? interpolate(frame, [12, frames - 20], [0, 1080], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) : 0;
  const target = clickTarget === "pool" ? { x: CONTENT_X + 1155, y: CONTENT_Y + 801 } : { x: CONTENT_X + 50, y: CONTENT_Y + 685 };
  const cursorSteps = clickTarget === "pool" ? [
    { frame: 30, x: CONTENT_X + 835, y: CONTENT_Y + 369 },
    { frame: 48, x: CONTENT_X + 835, y: CONTENT_Y + 369, click: true },
    { frame: clickAt - 24, ...target }, { frame: clickAt, ...target, click: true },
  ] : [{ frame: clickAt - 24, ...target }, { frame: clickAt, ...target, click: true }];

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame bassIntensity={bassIntensity}>
        <Sidebar active="Memory" />
        <div style={{ flex: 1, padding: "20px 32px", overflow: "hidden", fontFamily, color: COLORS.text }}>
          <div style={{ transform: `translateY(${-scroll}px)` }}>
            <PageHeader title="Memory" />
            <div style={{ fontSize: 19, lineHeight: 1.5, maxWidth: 1240 }}>{THESIS}</div>
            <div style={{ display: "flex", justifyContent: "center", gap: 28, borderBottom: `1px solid ${COLORS.border}`, margin: "14px 0 24px", fontSize: 13, fontWeight: 600 }}>
              <div style={{ padding: "12px 14px", borderBottom: `2px solid ${COLORS.primary}` }}>Storyboard</div>
              <div style={{ padding: "12px 14px", color: COLORS.textSecondary }}>Story</div>
            </div>
            <div style={{ fontSize: 14, fontWeight: 600 }}>{shots.length} pictures, {clock(shots.reduce((sum, s) => sum + s.seconds, 0))} of pictures and video, about {clock(film)} of film</div>
            <div style={{ fontSize: 12, color: COLORS.textSecondary, marginTop: 10 }}>Titles and transitions make up the rest of the film.</div>
            <div style={{ ...meta, margin: "18px 0 16px", alignItems: "center" }}>
              <span>Order and timecodes from the saved cut.</span><span>Show <span style={{ border: `1px solid ${COLORS.border}`, padding: "8px 10px", borderRadius: 6, marginLeft: 8 }}>All pictures ⌄</span></span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 320px", gap: 24, alignItems: "start" }}>
              <div>
                <div style={{ fontSize: 14, fontWeight: 600, margin: "0 0 18px" }}>June 2024</div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: "18px 16px" }}>
                  {shots.map((shot, index) => <ShotTile key={shot.picture} shot={shot} index={index} active={index === selected} />)}
                </div>
              </div>
              <div style={{ transform: `translateY(${Math.max(0, scroll - 240)}px)` }}><Inspector shot={shots[selected]} /></div>
            </div>
            <div style={{ display: "flex", gap: 16, borderTop: `1px solid ${COLORS.border}`, paddingTop: 16, marginTop: 24 }}>
              <ImButton text="Export" variant="primary" icon="movie" />
              <ImButton text="Review the pool" variant="secondary" icon="video_library" />
              <ImButton text="Cut again" variant="secondary" icon="refresh" />
              <ImButton text="Change the brief" variant="ghost" icon="edit" />
            </div>
          </div>
        </div>
      </WindowFrame>
      <AnimatedCursor steps={cursorSteps} />
    </AbsoluteFill>
  );
};
