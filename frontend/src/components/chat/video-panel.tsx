"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  PauseIcon,
  PlayIcon,
  RotateCcwIcon,
  SkipBackIcon,
  SkipForwardIcon,
  VideoOffIcon,
} from "lucide-react";

import { mediaUrl } from "@/lib/detection";
import { useChatStore } from "@/store/chat";

/**
 * The review panel: the attached video, its controls, and a chapter bar.
 *
 * The agent drives this. Every `control_video_playback` call it makes lands in
 * the chat store as a command, and the effect below applies it to the real
 * `<video>` element — so "show me the gun" actually seeks the player. Clicking
 * a chapter (or a timestamp in the transcript) issues the same kind of command,
 * which keeps manual and agent-driven playback on one code path.
 *
 * The bar works like YouTube's chapters: the whole runtime is cut into blocks
 * that tile it end to end, flagged spans take their category colour and the
 * clear stretches between them stay grey. A 0.5s–0.8s incident in a 10s clip is
 * therefore a red sliver you can see and click, not a full-width band. Picking
 * one plays exactly that span and unfolds its clip and explanation underneath.
 *
 * The caller keys this component on the video URL, so a new upload (or a switch
 * to another conversation) remounts it. That hands us a fresh `<video>` and
 * zeroed duration/position — without it the element keeps the previous media
 * and every chapter is placed against the old duration.
 */

/** Seconds -> m:ss, for the position readout. */
function fmt(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/**
 * Seconds -> m:ss.t. Chapter edges need the tenth: an incident running 0.5s to
 * 0.8s reads as "0:00 – 0:00" at whole-second precision.
 */
function fmtEdge(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return "0:00.0";
  const total = Math.round(seconds * 10) / 10;
  const m = Math.floor(total / 60);
  return `${m}:${(total - m * 60).toFixed(1).padStart(4, "0")}`;
}

/** One flagged span of the video, from whichever source reported it. */
interface Incident {
  key: string;
  start: number;
  end: number;
  label: string;
  category: string;
  description?: string | null;
  /** Plain-language readout of what is in the span (who and what is involved). */
  explanation?: string | null;
  clipUrl?: string | null;
  frameUrl?: string | null;
  confidence?: number;
}

/** A slice of the runtime: either a flagged span, or the clear gap before one. */
interface Chapter {
  start: number;
  end: number;
  incident: Incident | null;
}

/**
 * Every flagged span is red, whatever kind of incident it is — the bar answers
 * one question ("which seconds do I have to watch?") and a second colour axis
 * only blurs it. The category is named in the chapter list instead.
 */
const INCIDENT_COLOR = "bg-destructive";

/** Incidents from the agent's tool results — the stored, finished analysis. */
function fromMessages(
  messages: { role: string; tool_payload?: { result?: unknown } | null }[]
): Incident[] {
  const found: Incident[] = [];

  for (const message of messages) {
    if (message.role !== "tool") continue;
    const result = message.tool_payload?.result as
      | {
          segments?: {
            label: string;
            category: string;
            description?: string;
            start_time: number;
            end_time: number;
            confidence?: number;
            clip_url?: string | null;
            frame_url?: string | null;
          }[];
        }
      | undefined;

    for (const s of result?.segments ?? []) {
      found.push({
        key: `${s.start_time}-${s.end_time}-${s.label}`,
        start: s.start_time,
        end: s.end_time,
        label: s.label,
        category: s.category || "violence",
        description: s.description,
        clipUrl: s.clip_url,
        frameUrl: s.frame_url,
        confidence: s.confidence,
      });
    }
  }

  return found;
}

/** Object hits are instants, so they ride the bar as ticks, not as chapters. */
function detectionTicks(
  messages: { role: string; tool_payload?: { result?: unknown } | null }[]
): { second: number; label: string }[] {
  const ticks: { second: number; label: string }[] = [];
  for (const message of messages) {
    if (message.role !== "tool") continue;
    const result = message.tool_payload?.result as
      | { top_detections?: { second: number; label: string }[] }
      | undefined;
    for (const d of result?.top_detections ?? []) {
      ticks.push({ second: d.second, label: d.label });
    }
  }
  return ticks;
}

/**
 * Cut the runtime into chapters. Flagged spans keep their own block; whatever
 * is left between them becomes a "clear" one, so the blocks always tile the
 * full bar. Overlapping spans are clipped to the one that started first.
 */
function buildChapters(incidents: Incident[], duration: number): Chapter[] {
  if (duration <= 0) return [];

  const sorted = [...incidents]
    .map((i) => ({
      ...i,
      start: Math.max(0, Math.min(i.start, duration)),
      end: Math.max(0, Math.min(i.end, duration)),
    }))
    .sort((a, b) => a.start - b.start);

  const chapters: Chapter[] = [];
  let cursor = 0;

  for (const incident of sorted) {
    const start = Math.max(incident.start, cursor);
    // A zero-length span (one flagged frame) still deserves a visible block.
    const end = Math.min(duration, Math.max(incident.end, start + 0.2));
    if (start >= duration) break;
    if (end <= cursor) continue;

    if (start > cursor + 0.05) {
      chapters.push({ start: cursor, end: start, incident: null });
    }
    chapters.push({ start, end, incident });
    cursor = end;
  }

  if (cursor < duration - 0.05) {
    chapters.push({ start: cursor, end: duration, incident: null });
  }
  return chapters;
}

export function VideoPanel() {
  const detail = useChatStore((s) => s.detail);
  const live = useChatStore((s) => s.live);
  const playback = useChatStore((s) => s.playback);
  const command = useChatStore((s) => s.command);
  const submit = useChatStore((s) => s.submit);
  const sending = useChatStore((s) => s.sending);

  const videoRef = useRef<HTMLVideoElement>(null);
  /** Set while playing a bounded segment; cleared once we pause at its end. */
  const stopAtRef = useRef<number | null>(null);

  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  /** Which incident's clip and explanation are unfolded. */
  const [openKey, setOpenKey] = useState<string | null>(null);

  const src = mediaUrl(detail?.video_url);
  const messages = useMemo(() => detail?.messages ?? [], [detail?.messages]);
  const liveSegments = live?.segments;

  // Both sources describe the same incidents: the job streams them while the
  // analysis runs, the stored tool result carries them once it finishes. The
  // streamed one wins a tie — it is the one with the clip and the explanation.
  const incidents = useMemo<Incident[]>(() => {
    const byKey = new Map<string, Incident>();

    for (const incident of fromMessages(messages)) {
      byKey.set(`${incident.start.toFixed(1)}-${incident.end.toFixed(1)}`, incident);
    }
    for (const s of liveSegments ?? []) {
      byKey.set(`${s.start_time.toFixed(1)}-${s.end_time.toFixed(1)}`, {
        key: `live-${s.id}`,
        start: s.start_time,
        end: s.end_time,
        label: s.label,
        category: s.category || "violence",
        description: s.description,
        explanation: s.explanation,
        clipUrl: s.annotated_clip_url ?? s.clip_url,
        confidence: s.confidence,
      });
    }

    return [...byKey.values()].sort((a, b) => a.start - b.start);
  }, [messages, liveSegments]);

  const ticks = useMemo(() => detectionTicks(messages), [messages]);
  const chapters = useMemo(
    () => buildChapters(incidents, duration),
    [incidents, duration]
  );

  // Apply whatever the agent (or a click) last asked for. Keyed on `nonce` so
  // repeating an identical command still re-fires.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !playback) return;

    const { action, startTime, endTime } = playback;

    if (action === "pause") {
      stopAtRef.current = null;
      video.pause();
      return;
    }

    if (action === "replay") {
      stopAtRef.current = null;
      video.currentTime = 0;
      void video.play().catch(() => {});
      return;
    }

    if (action === "seek" || action === "play_segment") {
      video.currentTime = startTime ?? 0;
      stopAtRef.current = action === "play_segment" ? (endTime ?? null) : null;
    }

    void video.play().catch(() => {});
    // `playback` is replaced wholesale on each command, so this covers it.
  }, [playback]);

  if (!src) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
        <VideoOffIcon className="size-6 text-muted-foreground" />
        <p className="text-sm font-medium">No video attached</p>
        <p className="text-xs text-muted-foreground">
          Attach one with the paperclip and it will play here. Once it has been
          analysed, the bar underneath marks every incident in its own colour.
        </p>
      </div>
    );
  }

  function issue(cmd: Parameters<typeof command>[0]) {
    command(cmd);
  }

  function nudge(delta: number) {
    const video = videoRef.current;
    if (!video) return;
    issue({
      action: "seek",
      startTime: Math.max(0, Math.min(video.currentTime + delta, duration || 0)),
    });
  }

  /** Play one chapter and unfold what it contains. */
  function openChapter(chapter: Chapter) {
    issue({
      action: "play_segment",
      startTime: chapter.start,
      endTime: chapter.end,
      reason: chapter.incident?.label,
    });
    setOpenKey(chapter.incident?.key ?? null);
  }

  /** Ask the agent to talk one span through, in the conversation. */
  function explain(incident: Incident) {
    void submit(
      `Explain what happens between ${incident.start.toFixed(1)}s and ` +
        `${incident.end.toFixed(1)}s of the video — you flagged it as ` +
        `"${incident.label}". Describe who and what is involved.`
    ).catch(() => {});
  }

  const activeChapter = chapters.find(
    (c) => currentTime >= c.start && currentTime < c.end
  );
  const openIncident =
    incidents.find((i) => i.key === openKey) ?? activeChapter?.incident ?? null;

  return (
    <div className="flex h-full min-h-0 w-full flex-col">
      <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
        <h2 className="truncate text-sm font-medium">Video review</h2>
        {playing && (
          <span className="ms-auto text-xs text-muted-foreground">playing</span>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <video
          ref={videoRef}
          src={src}
          controls
          preload="metadata"
          className="w-full rounded-xl border bg-black"
          onLoadedMetadata={(e) => setDuration(e.currentTarget.duration || 0)}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onTimeUpdate={(e) => {
            const video = e.currentTarget;
            setCurrentTime(video.currentTime);
            // Honour the end of an agent-requested segment.
            const stopAt = stopAtRef.current;
            if (stopAt !== null && video.currentTime >= stopAt) {
              stopAtRef.current = null;
              video.pause();
            }
          }}
        />

        <p className="mt-2 truncate text-xs text-muted-foreground">
          {detail?.video_name}
        </p>

        {/* Transport — the same commands the agent issues */}
        <div className="mt-3 flex items-center gap-1">
          <button
            type="button"
            onClick={() => issue({ action: "replay" })}
            title="Replay from the start"
            aria-label="Replay from the start"
            className="grid size-9 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <RotateCcwIcon className="size-4" />
          </button>
          <button
            type="button"
            onClick={() => nudge(-5)}
            title="Back 5 seconds"
            aria-label="Back 5 seconds"
            className="grid size-9 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <SkipBackIcon className="size-4" />
          </button>
          <button
            type="button"
            onClick={() => issue({ action: playing ? "pause" : "play" })}
            title={playing ? "Pause" : "Play"}
            aria-label={playing ? "Pause" : "Play"}
            className="grid size-9 place-items-center rounded-full bg-primary text-primary-foreground transition-opacity hover:opacity-90"
          >
            {playing ? <PauseIcon className="size-4" /> : <PlayIcon className="size-4" />}
          </button>
          <button
            type="button"
            onClick={() => nudge(5)}
            title="Forward 5 seconds"
            aria-label="Forward 5 seconds"
            className="grid size-9 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <SkipForwardIcon className="size-4" />
          </button>

          <span className="ms-auto font-mono text-xs text-muted-foreground">
            {fmt(currentTime)} / {fmt(duration)}
          </span>
        </div>

        {/* Chapter bar — the whole runtime, incidents in colour */}
        {duration > 0 && (
          <div className="mt-4">
            <div className="mb-1.5 flex items-center gap-2">
              <p className="text-xs font-medium text-muted-foreground">
                {incidents.length > 0
                  ? `Chapters — ${incidents.length} incident${incidents.length > 1 ? "s" : ""}`
                  : "Chapters"}
              </p>
              {live?.status === "running" && (
                <span className="text-xs text-muted-foreground">
                  still analysing… {live.progress}%
                </span>
              )}
            </div>

            <div className="relative">
              <div className="flex h-7 w-full gap-0.5 overflow-hidden rounded-md">
                {chapters.length === 0 ? (
                  <div className="h-full w-full bg-muted" />
                ) : (
                  chapters.map((chapter, i) => {
                    const width = ((chapter.end - chapter.start) / duration) * 100;
                    const played = Math.max(
                      0,
                      Math.min(
                        1,
                        (currentTime - chapter.start) / (chapter.end - chapter.start)
                      )
                    );
                    const incident = chapter.incident;
                    const range = `${fmtEdge(chapter.start)}–${fmtEdge(chapter.end)}`;
                    return (
                      <button
                        key={`${chapter.start}-${i}`}
                        type="button"
                        onClick={() => openChapter(chapter)}
                        title={incident ? `${incident.label} · ${range}` : `Clear · ${range}`}
                        aria-label={
                          incident
                            ? `Play ${incident.label} from ${fmtEdge(chapter.start)}`
                            : `Play the clear stretch from ${fmtEdge(chapter.start)}`
                        }
                        style={{ flexBasis: `${width}%` }}
                        className={[
                          // Basis is the chapter's share of the runtime. Shrink
                          // stays on so the gaps come out of the blocks instead
                          // of overflowing the bar, and min-width keeps a
                          // sub-second incident clickable.
                          "relative h-full min-w-[6px] grow-0 overflow-hidden transition-opacity",
                          incident
                            ? `${INCIDENT_COLOR} opacity-90 hover:opacity-100`
                            : "bg-muted hover:bg-muted-foreground/20",
                          chapter === activeChapter ? "ring-1 ring-inset ring-foreground/40" : "",
                        ].join(" ")}
                      >
                        {/* How far playback has got through this chapter. */}
                        <span
                          className={`absolute inset-y-0 start-0 ${
                            incident ? "bg-foreground/25" : "bg-foreground/10"
                          }`}
                          style={{ width: `${played * 100}%` }}
                        />
                      </button>
                    );
                  })
                )}
              </div>

              {/* Weapon hits are instants, so they sit on top as thin ticks. */}
              {ticks.map((tick, i) => (
                <span
                  key={`tick-${tick.second}-${i}`}
                  title={`${tick.label} @ ${fmtEdge(tick.second)}`}
                  className="pointer-events-none absolute top-0 h-full w-0.5 bg-destructive"
                  style={{ left: `${Math.min(100, (tick.second / duration) * 100)}%` }}
                />
              ))}

              <span
                className="pointer-events-none absolute top-0 z-10 h-full w-0.5 bg-foreground"
                style={{ left: `${Math.min(100, (currentTime / duration) * 100)}%` }}
              />
            </div>

            <div className="mt-1 flex justify-between text-xs text-muted-foreground">
              <span>0:00</span>
              <span>{fmt(duration)}</span>
            </div>

            {incidents.length === 0 && (
              <p className="mt-2 text-xs text-muted-foreground">
                {live?.status === "running"
                  ? "Incidents appear here as the detectors find them."
                  : "No incident sections — the whole clip came back clear."}
              </p>
            )}

            {/* The chapter list, the way a description box lists chapters */}
            {incidents.length > 0 && (
              <ul className="mt-3 space-y-1">
                {chapters.map((chapter, i) => {
                  const incident = chapter.incident;
                  const active = chapter === activeChapter;
                  return (
                    <li key={`row-${chapter.start}-${i}`}>
                      <button
                        type="button"
                        onClick={() => openChapter(chapter)}
                        className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-start text-xs transition-colors hover:bg-muted ${
                          active ? "bg-muted" : ""
                        }`}
                      >
                        <span
                          className={`size-2 shrink-0 rounded-full ${
                            incident ? INCIDENT_COLOR : "bg-muted-foreground/40"
                          }`}
                        />
                        <span className="shrink-0 font-mono text-muted-foreground">
                          {fmtEdge(chapter.start)}–{fmtEdge(chapter.end)}
                        </span>
                        <span
                          className={`truncate ${incident ? "font-medium" : "text-muted-foreground"}`}
                        >
                          {incident ? incident.label : "Clear"}
                        </span>
                        {incident && (
                          <span className="shrink-0 text-muted-foreground">
                            {incident.category}
                          </span>
                        )}
                        {incident?.confidence !== undefined && (
                          <span className="ms-auto shrink-0 text-muted-foreground">
                            {Math.round(incident.confidence * 100)}%
                          </span>
                        )}
                      </button>

                      {/* The clip itself, and what happens in it */}
                      {incident && incident.key === openIncident?.key && (
                        <div className="mb-2 ms-4 space-y-2 border-s ps-3">
                          {incident.clipUrl ? (
                            <video
                              src={mediaUrl(incident.clipUrl)}
                              controls
                              preload="metadata"
                              className="w-full rounded-lg border bg-black"
                            />
                          ) : incident.frameUrl ? (
                            /* eslint-disable-next-line @next/next/no-img-element --
                               stills are written at request time under /media,
                               so there is nothing to prebuild. */
                            <img
                              src={mediaUrl(incident.frameUrl)}
                              alt={`${incident.label} at ${fmtEdge(incident.start)}`}
                              loading="lazy"
                              className="w-full rounded-lg border"
                            />
                          ) : null}

                          <p className="text-xs text-muted-foreground">
                            {incident.explanation ||
                              incident.description ||
                              `${incident.label} (${incident.category}) runs from ${fmtEdge(incident.start)} to ${fmtEdge(incident.end)}.`}
                          </p>

                          <button
                            type="button"
                            onClick={() => explain(incident)}
                            disabled={sending}
                            className="rounded-lg border px-2 py-1 text-xs transition-colors hover:bg-muted disabled:opacity-50"
                          >
                            Explain this in the chat
                          </button>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}

        {playback?.reason && (
          <p className="mt-4 rounded-lg border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
            {playback.reason}
          </p>
        )}
      </div>
    </div>
  );
}
