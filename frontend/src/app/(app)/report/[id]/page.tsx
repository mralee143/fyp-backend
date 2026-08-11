"use client";

import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { IncidentTimeline } from "@/components/incident-timeline";
import { ScanChatPanel, type IncidentFocus } from "@/components/scan-chat-panel";
import { errorMessage } from "@/lib/auth";
import { getScan, mediaUrl, type ScanDetail } from "@/lib/detection";
import { getAdminScan } from "@/lib/admin";

function fmtTime(s: number): string {
  if (!isFinite(s)) return "0:00";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

function catClasses(category: string): string {
  switch (category) {
    case "theft":
      return "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400";
    case "harassment":
      return "border-purple-500/40 bg-purple-500/10 text-purple-600 dark:text-purple-400";
    case "accident":
      return "border-blue-500/40 bg-blue-500/10 text-blue-600 dark:text-blue-400";
    case "violence":
      return "border-destructive/40 bg-destructive/10 text-destructive";
    default:
      return "border-zinc-500/40 bg-zinc-500/10 text-foreground";
  }
}

interface Moment {
  start: number;
  end: number;
  label: string;
  category: string;
  text: string;
  confidence: number;
  clipUrl?: string;
  /** Set for stored incidents — how the chat agent addresses this one. */
  ordinal?: number;
}

export default function ReportPage() {
  const params = useParams();
  const id = Number(params?.id);

  const [scan, setScan] = useState<ScanDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const videoRef = useRef<HTMLVideoElement>(null);
  const chatRef = useRef<HTMLDivElement>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [playerDuration, setPlayerDuration] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const [focus, setFocus] = useState<IncidentFocus | null>(null);

  // The (app) layout guards the route, so this only runs when authenticated.
  useEffect(() => {
    // Own scan first; fall back to the admin endpoint (any user's scan).
    getScan(id)
      .then(setScan)
      .catch(() =>
        getAdminScan(id)
          .then(setScan)
          .catch((e) => setError(errorMessage(e)))
      );
  }, [id]);

  if (error) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-12">
        <p className="text-destructive">{error}</p>
        <Button
          render={<Link href="/dashboard" />}
          nativeButton={false}
          variant="outline"
          className="mt-4"
        >
          ← Back to dashboard
        </Button>
      </div>
    );
  }

  if (!scan) {
    return <div className="px-6 py-12 text-muted-foreground">Loading report…</div>;
  }

  const detected = scan.violence_detected;
  const segments = scan.result?.segments ?? [];
  const detections = scan.result?.detections ?? [];
  // Sampled stills only — incident cover images already appear on their clip.
  const frames = (scan.images ?? []).filter((image) => image.kind === "FRAME");

  const moments: Moment[] = segments.length
    ? segments.map((s) => ({
        start: s.start_time,
        end: s.end_time,
        label: s.label,
        category: s.category || "violence",
        text: s.explanation || s.description,
        confidence: s.confidence,
        clipUrl: mediaUrl(s.clip_url),
        ordinal: s.ordinal,
      }))
    : detections.map((d) => ({
        start: d.second,
        end: d.second,
        label: d.label,
        category: "violence",
        text: `Detected ${d.label}`,
        confidence: d.score,
      }));

  const videoUrl = mediaUrl(scan.video?.url);
  // The stored probe is authoritative; the player's own duration covers a scan
  // whose probe failed, and the last incident covers neither being available.
  const duration = scan.video?.duration_seconds || playerDuration || 0;

  /** Move the player to a moment and mark it as the one under discussion. */
  function goTo(second: number, ordinal?: number) {
    if (videoRef.current) {
      videoRef.current.currentTime = second;
    }
    setCurrentTime(second);
    if (ordinal !== undefined) setSelected(ordinal);
  }

  /** Hand one incident to the agent and scroll the conversation into view. */
  function askAbout(moment: Moment) {
    if (moment.ordinal === undefined) return;
    goTo(moment.start, moment.ordinal);
    setFocus({
      ordinal: moment.ordinal,
      label: moment.label,
      start: moment.start,
      end: moment.end,
      clipUrl: moment.clipUrl,
      // Re-clicking the same incident must re-run, so the request is keyed by
      // click rather than by which incident it is.
      token: Date.now(),
    });
    chatRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-8">
        <p className="text-sm text-muted-foreground">
          {scan.filename} · {scan.model} · {new Date(scan.created_at).toLocaleString()}
        </p>

        <div
          className={`mt-4 rounded-xl border p-4 ${
            detected
              ? "border-destructive/40 bg-destructive/10"
              : "border-green-500/40 bg-green-500/10"
          }`}
        >
          <p className="text-lg font-semibold">
            {detected ? "⚠️ Incident detected" : "✅ No incident found"}
          </p>
          {scan.summary && (
            <p className="mt-1 text-sm text-muted-foreground">{scan.summary}</p>
          )}
        </div>

        {/* The whole video with its flagged spans drawn on it — where an
            incident sits matters as much as that there was one. */}
        <div className="mt-8 rounded-xl border p-4">
          {videoUrl && (
            <video
              ref={videoRef}
              src={videoUrl}
              controls
              preload="metadata"
              onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
              onLoadedMetadata={(e) => setPlayerDuration(e.currentTarget.duration)}
              className="mb-4 w-full rounded-lg border bg-black"
            />
          )}
          <IncidentTimeline
            duration={duration}
            currentTime={currentTime}
            selectedKey={selected}
            incidents={moments.map((m, i) => ({
              key: m.ordinal ?? i,
              start: m.start,
              end: m.end,
              label: m.label,
              category: m.category,
              confidence: m.confidence,
            }))}
            onSeek={(second) => goTo(second)}
            onSelect={(incident) => {
              const moment = moments.find(
                (m) => (m.ordinal ?? moments.indexOf(m)) === incident.key
              );
              if (moment) askAbout(moment);
            }}
          />
          {moments.length > 0 && (
            <p className="mt-3 text-xs text-muted-foreground">
              Click a marked section to jump there and have the assistant read
              that clip.
            </p>
          )}
          {!videoUrl && (
            <p className="mt-3 text-xs text-muted-foreground">
              This analysis ran before the video was stored, so there is nothing
              to play back — the timings above are still what was detected.
            </p>
          )}
        </div>

        <h2 className="mt-8 text-lg font-semibold">
          {moments.length > 0
            ? `Flagged sections (${moments.length})`
            : "No flagged sections"}
        </h2>

        <div className="mt-3 space-y-4">
          {moments.map((m, i) => (
            <div
              key={i}
              className={`rounded-xl border p-4 transition-colors ${
                selected !== null && m.ordinal === selected ? "border-foreground" : ""
              }`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => goTo(m.start, m.ordinal)}
                  className="font-mono text-sm text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                >
                  {fmtTime(m.start)}
                  {m.end > m.start ? `–${fmtTime(m.end)}` : ""}
                </button>
                <span className="font-medium">{m.label}</span>
                <span
                  className={`rounded-full border px-2 py-0.5 text-xs ${catClasses(
                    m.category
                  )}`}
                >
                  {m.category}
                </span>
                <span className="text-xs text-muted-foreground">
                  {(m.confidence * 100).toFixed(0)}%
                </span>
                {m.ordinal !== undefined && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="ml-auto"
                    onClick={() => askAbout(m)}
                  >
                    Ask the assistant →
                  </Button>
                )}
              </div>
              {m.text && (
                <p className="mt-1 text-sm text-muted-foreground">{m.text}</p>
              )}
              {m.clipUrl && (
                <video
                  src={m.clipUrl}
                  controls
                  preload="metadata"
                  className="mt-3 w-full rounded-lg border bg-black sm:max-w-md"
                />
              )}
            </div>
          ))}
        </div>

        {/* Frames stored for this video, each with the caption written while
            the analysis ran. */}
        {frames.length > 0 && (
          <>
            <h2 className="mt-10 text-lg font-semibold">
              Extracted frames ({frames.length})
            </h2>
            <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
              {frames.map((frame) => (
                <figure
                  key={frame.id}
                  className={`overflow-hidden rounded-lg border bg-muted/30 ${
                    frame.segment_id ? "ring-2 ring-destructive/40" : ""
                  }`}
                >
                  {frame.url && (
                    /* eslint-disable-next-line @next/next/no-img-element --
                       presigned MinIO URLs expire, so they cannot be optimised
                       at build time. */
                    <img
                      src={frame.url}
                      alt={frame.caption ?? `Frame at ${fmtTime(frame.captured_at_seconds ?? 0)}`}
                      loading="lazy"
                      className="aspect-video w-full object-cover"
                    />
                  )}
                  <figcaption className="p-2">
                    <span className="font-mono text-[11px] text-muted-foreground">
                      {fmtTime(frame.captured_at_seconds ?? 0)}
                    </span>
                    {frame.caption && (
                      <p className="mt-0.5 line-clamp-2 text-xs">{frame.caption}</p>
                    )}
                  </figcaption>
                </figure>
              ))}
            </div>
          </>
        )}

        {/* Ask the agent about this specific analysis — or, when an incident is
            focused, about that clip alone. */}
        <div ref={chatRef} className="mt-10 scroll-mt-6">
          <ScanChatPanel
            scanId={scan.id}
            focus={focus}
            onClearFocus={() => setFocus(null)}
          />
        </div>
      </div>
  );
}
