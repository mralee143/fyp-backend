"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { useAuthStore } from "@/store/auth";
import { useScanStore } from "@/store/scan";
import { mediaUrl } from "@/lib/detection";

function fmtTime(s: number): string {
  if (!isFinite(s)) return "0:00";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

// Colour per event category.
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
function catBar(category: string): string {
  switch (category) {
    case "theft":
      return "bg-amber-500";
    case "harassment":
      return "bg-purple-500";
    case "accident":
      return "bg-blue-500";
    case "violence":
      return "bg-destructive";
    default:
      return "bg-zinc-500";
  }
}

/** Unified "moment" the timeline/list renders, from either detection type. */
interface Moment {
  start: number;
  end: number;
  label: string;
  category: string;
  description: string;
  confidence: number;
  clipUrl?: string;
}

export default function ResultsPage() {
  const router = useRouter();
  const videoRef = useRef<HTMLVideoElement>(null);

  const videoUrl = useScanStore((s) => s.videoUrl);
  const videoName = useScanStore((s) => s.videoName);
  const objectResult = useScanStore((s) => s.objectResult);
  const llmResult = useScanStore((s) => s.llmResult);

  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [ready, setReady] = useState(false);

  // Protect the route + require a scan to be present.
  useEffect(() => {
    if (!useAuthStore.getState().token) {
      router.replace("/login");
    } else if (!useScanStore.getState().videoUrl) {
      router.replace("/dashboard");
    } else {
      setReady(true);
    }
  }, [router]);

  if (!ready || !videoUrl) return null;

  function seekTo(seconds: number) {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = seconds;
    void v.play().catch(() => {});
    v.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // Build the unified moment list + verdict from whichever result we have.
  let moments: Moment[] = [];
  let detected = false;
  let summary = "";
  let modelId = "";

  if (llmResult) {
    detected = llmResult.violence_detected;
    summary = llmResult.summary;
    modelId = llmResult.model_id;
    moments = llmResult.segments.map((s) => ({
      start: s.start_time,
      end: s.end_time,
      label: s.label,
      category: s.category || "violence",
      description: s.explanation || s.description,
      confidence: s.confidence,
      clipUrl: mediaUrl(s.clip_url),
    }));
  } else if (objectResult) {
    detected = objectResult.weapon_detected;
    modelId = objectResult.model_id;
    summary = `${objectResult.detection_count} detection(s) across ${objectResult.frames_scanned} sampled frames.`;
    moments = objectResult.detections.map((d) => ({
      start: d.second,
      end: d.second,
      label: d.label,
      category: "violence",
      description: `Detected ${d.label}`,
      confidence: d.score,
    }));
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-2 font-semibold">
            <span className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground">
              S
            </span>
            Detection results
          </div>
          <Button
            render={<Link href="/dashboard" />}
            nativeButton={false}
            variant="outline"
            size="sm"
          >
            ← New scan
          </Button>
        </div>
      </header>

      <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-8">
        {videoName && (
          <p className="mb-4 truncate text-sm text-muted-foreground">
            {videoName}
          </p>
        )}

        {/* Verdict */}
        <div
          className={`rounded-xl border p-4 ${
            detected
              ? "border-destructive/40 bg-destructive/10"
              : "border-green-500/40 bg-green-500/10"
          }`}
        >
          <p className="text-lg font-semibold">
            {detected ? "⚠️ Violence detected" : "✅ No violence found"}
          </p>
          {summary && (
            <p className="mt-1 text-sm text-muted-foreground">{summary}</p>
          )}
          <p className="mt-1 text-xs text-muted-foreground">model {modelId}</p>
        </div>

        {/* Player */}
        <video
          ref={videoRef}
          src={videoUrl}
          controls
          preload="metadata"
          className="mt-6 w-full rounded-xl border bg-black"
          onLoadedMetadata={(e) => setDuration(e.currentTarget.duration || 0)}
          onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
        />

        {/* Timeline — where in the clip the events are */}
        {duration > 0 && moments.length > 0 && (
          <div className="mt-4">
            <div className="relative h-6 w-full overflow-hidden rounded-full bg-muted">
              {/* current playback position */}
              <div
                className="absolute top-0 z-10 h-full w-0.5 bg-foreground"
                style={{ left: `${(currentTime / duration) * 100}%` }}
              />
              {moments.map((m, i) => {
                const left = (m.start / duration) * 100;
                const width = Math.max(((m.end - m.start) / duration) * 100, 1.2);
                return (
                  <button
                    key={i}
                    title={`${m.label} @ ${fmtTime(m.start)}`}
                    onClick={() => seekTo(m.start)}
                    className={`absolute top-0 h-full cursor-pointer ${catBar(m.category)} opacity-80 hover:opacity-100`}
                    style={{ left: `${left}%`, width: `${width}%` }}
                  />
                );
              })}
            </div>
            <div className="mt-1 flex justify-between text-xs text-muted-foreground">
              <span>0:00</span>
              <span>{fmtTime(duration)}</span>
            </div>
          </div>
        )}

        {/* Sections list */}
        <h2 className="mt-8 text-lg font-semibold">
          {moments.length > 0
            ? `Flagged sections (${moments.length})`
            : "No flagged sections"}
        </h2>

        <div className="mt-3 space-y-4">
          {moments.map((m, i) => (
            <div key={i} className="rounded-xl border p-4">
              <div className="flex items-start gap-4">
                <div className="shrink-0 font-mono text-sm text-muted-foreground">
                  {fmtTime(m.start)}
                  {m.end > m.start ? `–${fmtTime(m.end)}` : ""}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
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
                  </div>
                  {m.description && (
                    <p className="mt-1 text-sm text-muted-foreground">
                      {m.description}
                    </p>
                  )}
                </div>
                <button
                  onClick={() => seekTo(m.start)}
                  className="shrink-0 self-center text-sm text-primary hover:underline"
                >
                  ▶ Jump
                </button>
              </div>

              {/* The extracted clip of just this incident */}
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
      </main>
    </div>
  );
}
