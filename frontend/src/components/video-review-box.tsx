"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ChevronsLeftIcon,
  ChevronsRightIcon,
  PauseIcon,
  PlayIcon,
  RotateCcwIcon,
  SkipBackIcon,
  SkipForwardIcon,
} from "lucide-react";

import { mediaUrl } from "@/lib/detection";
import type { PlaybackCommand } from "@/store/chat";

/**
 * The review box: a player, its transport, and a chapter bar over the runtime.
 *
 * This is the one place the review UI is built. The chat panel drives it with
 * the agent's `control_video_playback` commands; the scan results page renders
 * the very same box over a finished scan, so an incident reads the same way
 * wherever you opened it from.
 *
 * The bar works like YouTube's chapters: the whole runtime is cut into blocks
 * that tile it end to end, flagged spans take the incident colour and the clear
 * stretches between them stay grey. A 0.5s–0.8s incident in a 10s clip is
 * therefore a red sliver you can see and click, not a full-width band. Picking
 * one plays exactly that span and unfolds its clip and explanation underneath.
 *
 * Key this component on the video URL so a new source remounts it. Without that
 * the element keeps the previous media and every chapter is placed against the
 * old duration.
 */

/** Seconds -> m:ss, for the position readout. */
export function fmt(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/**
 * Seconds -> m:ss.t. Chapter edges need the tenth: an incident running 0.5s to
 * 0.8s reads as "0:00 – 0:00" at whole-second precision.
 */
export function fmtEdge(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return "0:00.0";
  const total = Math.round(seconds * 10) / 10;
  const m = Math.floor(total / 60);
  return `${m}:${(total - m * 60).toFixed(1).padStart(4, "0")}`;
}

/** One flagged span of the video, from whichever source reported it. */
export interface ReviewIncident {
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

/** An instant hit (an object detection), drawn as a tick on the bar. */
export interface ReviewTick {
  second: number;
  label: string;
}

/** A slice of the runtime: either a flagged span, or the clear gap before one. */
interface Chapter {
  start: number;
  end: number;
  incident: ReviewIncident | null;
}

/**
 * Every flagged span is red, whatever kind of incident it is — the bar answers
 * one question ("which seconds do I have to watch?") and a second colour axis
 * only blurs it. The category is named in the chapter list instead.
 */
const INCIDENT_COLOR = "bg-destructive";

/**
 * Cut the runtime into chapters. Flagged spans keep their own block; whatever
 * is left between them becomes a "clear" one, so the blocks always tile the
 * full bar. Overlapping spans are clipped to the one that started first.
 */
export function buildChapters(
  incidents: ReviewIncident[],
  duration: number
): Chapter[] {
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

interface VideoReviewBoxProps {
  /**
   * Already resolved to an absolute URL by the caller. Leave it unset when
   * there is no video yet: the box keeps its header (and so its arrow) and
   * shows `emptyState` in place of the player.
   */
  src?: string | null;
  /** File name, shown under the player. */
  name?: string | null;
  incidents: ReviewIncident[];
  ticks?: ReviewTick[];
  title?: string;
  /** Anything the caller wants beside the title — progress, a verdict, a count. */
  headerNote?: ReactNode;
  /** Sentence under the chapter bar when there is nothing flagged. */
  emptyNote?: ReactNode;
  /** Shown instead of the player while there is no video to review. */
  emptyState?: ReactNode;
  /**
   * Playback issued from outside (the agent's commands). Replaced wholesale on
   * every command, `nonce` included, so repeating one still re-fires.
   */
  playback?: PlaybackCommand | null;
  /**
   * Where the box's own clicks go. Chat routes them through the store, so
   * manual and agent-driven playback stay on one code path; without it the box
   * drives its player directly.
   */
  onCommand?: (cmd: Omit<PlaybackCommand, "nonce">) => void;
  /** Offered on each incident when given — chat uses it to ask the agent. */
  onExplain?: (incident: ReviewIncident) => void;
  explainDisabled?: boolean;
  explainLabel?: string;
  /**
   * The widen arrow. Rendered only when `onToggleExpanded` is given, since only
   * a caller that can actually grow (the chat side panel) has somewhere to go.
   */
  expanded?: boolean;
  onToggleExpanded?: () => void;
}

export function VideoReviewBox({
  src,
  name,
  incidents,
  ticks = [],
  title = "Video review",
  headerNote,
  emptyNote,
  emptyState,
  playback,
  onCommand,
  onExplain,
  explainDisabled = false,
  explainLabel = "Explain this in the chat",
  expanded = false,
  onToggleExpanded,
}: VideoReviewBoxProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  /** Set while playing a bounded segment; cleared once we pause at its end. */
  const stopAtRef = useRef<number | null>(null);
  /** Commands the box issued itself, when the caller does not route them. */
  const [localPlayback, setLocalPlayback] = useState<PlaybackCommand | null>(null);
  const nonceRef = useRef(0);

  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  /** Which incident's clip and explanation are unfolded. */
  const [openKey, setOpenKey] = useState<string | null>(null);

  const chapters = useMemo(
    () => buildChapters(incidents, duration),
    [incidents, duration]
  );

  const active = onCommand ? playback : localPlayback;

  // Apply whatever was last asked for. Keyed on the command object, which is
  // replaced (nonce and all) per request, so an identical repeat still fires.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !active) return;

    const { action, startTime, endTime } = active;

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
  }, [active]);

  function issue(cmd: Omit<PlaybackCommand, "nonce">) {
    if (onCommand) {
      onCommand(cmd);
      return;
    }
    setLocalPlayback({ ...cmd, nonce: ++nonceRef.current });
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

  const activeChapter = chapters.find(
    (c) => currentTime >= c.start && currentTime < c.end
  );
  const openIncident =
    incidents.find((i) => i.key === openKey) ?? activeChapter?.incident ?? null;

  const header = (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
        {onToggleExpanded && (
          <button
            type="button"
            onClick={onToggleExpanded}
            aria-expanded={expanded}
            title={expanded ? "Shrink the video panel" : "Widen the video panel"}
            aria-label={expanded ? "Shrink the video panel" : "Widen the video panel"}
            className="grid size-7 shrink-0 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            {expanded ? (
              <ChevronsRightIcon className="size-4" />
            ) : (
              <ChevronsLeftIcon className="size-4" />
            )}
          </button>
        )}
        <h2 className="truncate text-sm font-medium">{title}</h2>
      {headerNote ? (
        <span className="ms-auto shrink-0 text-xs text-muted-foreground">
          {headerNote}
        </span>
      ) : (
        playing && (
          <span className="ms-auto text-xs text-muted-foreground">playing</span>
        )
      )}
    </header>
  );

  if (!src) {
    return (
      <div className="flex h-full min-h-0 w-full flex-col">
        {header}
        <div className="flex min-h-0 flex-1 items-center justify-center p-6">
          {emptyState}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 w-full flex-col">
      {header}

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
            // Honour the end of a requested segment.
            const stopAt = stopAtRef.current;
            if (stopAt !== null && video.currentTime >= stopAt) {
              stopAtRef.current = null;
              video.pause();
            }
          }}
        />

        {name && (
          <p className="mt-2 truncate text-xs text-muted-foreground">{name}</p>
        )}

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

            {incidents.length === 0 && emptyNote && (
              <p className="mt-2 text-xs text-muted-foreground">{emptyNote}</p>
            )}

            {/* The chapter list, the way a description box lists chapters */}
            {incidents.length > 0 && (
              <ul className="mt-3 space-y-1">
                {chapters.map((chapter, i) => {
                  const incident = chapter.incident;
                  const isActive = chapter === activeChapter;
                  return (
                    <li key={`row-${chapter.start}-${i}`}>
                      <button
                        type="button"
                        onClick={() => openChapter(chapter)}
                        className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-start text-xs transition-colors hover:bg-muted ${
                          isActive ? "bg-muted" : ""
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

                          {onExplain && (
                            <button
                              type="button"
                              onClick={() => onExplain(incident)}
                              disabled={explainDisabled}
                              className="rounded-lg border px-2 py-1 text-xs transition-colors hover:bg-muted disabled:opacity-50"
                            >
                              {explainLabel}
                            </button>
                          )}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}

        {active?.reason && (
          <p className="mt-4 rounded-lg border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
            {active.reason}
          </p>
        )}
      </div>
    </div>
  );
}
