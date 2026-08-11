"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { IncidentTimeline } from "@/components/incident-timeline";
import { errorMessage } from "@/lib/auth";
import {
  createJob,
  formatTime,
  getJob,
  stageLabel,
  subscribeToJob,
  validateVideo,
  type DetectionModel,
  type JobEvent,
  type JobFrame,
  type JobSegment,
} from "@/lib/jobs";

/**
 * Upload a video and watch it being analysed.
 *
 * The upload call returns in about a second; everything after that arrives by
 * push. Extracted frames appear within a second or two — well before the
 * detection models have finished — so the page shows the actual video content
 * rather than a spinner.
 */

const MODELS: { value: DetectionModel; label: string; hint: string }[] = [
  {
    value: "auto",
    label: "Auto",
    hint: "All six models in turn — best coverage, slowest",
  },
  { value: "yolo", label: "Weapons", hint: "Fast, local: guns, knives, grenades" },
  { value: "owlv2", label: "Objects", hint: "Zero-shot: describe what to find" },
  { value: "llm", label: "Gemini", hint: "Understands context and harassment" },
  { value: "action", label: "Actions", hint: "Fighting, robbery, accidents" },
  { value: "qwen", label: "Qwen (local)", hint: "Offline, slow but thorough" },
];

function categoryClasses(category: string): string {
  switch (category) {
    case "theft":
      return "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400";
    case "harassment":
      return "border-purple-500/40 bg-purple-500/10 text-purple-600 dark:text-purple-400";
    case "accident":
      return "border-blue-500/40 bg-blue-500/10 text-blue-600 dark:text-blue-400";
    case "violence":
      return "border-destructive/40 bg-destructive/10 text-destructive";
    case "weapon":
      return "border-orange-500/40 bg-orange-500/10 text-orange-600 dark:text-orange-400";
    default:
      return "border-zinc-500/40 bg-zinc-500/10 text-foreground";
  }
}

export function LiveAnalysis() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const subscriptionRef = useRef<{ close: () => void } | null>(null);
  const previewRef = useRef<HTMLVideoElement>(null);
  const previewUrlRef = useRef<string | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const [model, setModel] = useState<DetectionModel>("auto");
  const [queries, setQueries] = useState("");
  const [dragging, setDragging] = useState(false);

  const [jobId, setJobId] = useState<string | null>(null);
  const [uploadPercent, setUploadPercent] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState<string | null>(null);
  const [message, setMessage] = useState<string>("");
  const [frames, setFrames] = useState<JobFrame[]>([]);
  const [segments, setSegments] = useState<JobSegment[]>([]);
  const [finished, setFinished] = useState<"ok" | "failed" | null>(null);
  const [scanId, setScanId] = useState<number | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);

  // Always drop the stream on unmount, or the browser keeps reconnecting — and
  // release the preview, which pins the whole file in memory until revoked.
  useEffect(
    () => () => {
      subscriptionRef.current?.close();
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    },
    []
  );

  /**
   * Play the chosen file straight from disk: the incident bar needs something
   * to sit under, and the upload is not addressable until the job finishes.
   */
  function showPreview(candidate: File) {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = URL.createObjectURL(candidate);
    setPreviewUrl(previewUrlRef.current);
    setDuration(0);
  }

  const running = uploading || (jobId !== null && finished === null);

  const reset = useCallback(() => {
    subscriptionRef.current?.close();
    subscriptionRef.current = null;
    setJobId(null);
    setProgress(0);
    setUploadPercent(0);
    setStage(null);
    setMessage("");
    setFrames([]);
    setSegments([]);
    setFinished(null);
    setScanId(null);
    setCurrentTime(0);
  }, []);

  function pickFile(candidate: File | undefined) {
    if (!candidate) return;
    const invalid = validateVideo(candidate);
    if (invalid) {
      toast.error(invalid);
      return;
    }
    reset();
    setFile(candidate);
    showPreview(candidate);
  }

  const handleEvent = useCallback((event: JobEvent) => {
    setProgress(event.progress ?? 0);
    setStage(event.stage);
    if (event.message) setMessage(event.message);
  }, []);

  async function start() {
    if (!file) return;

    reset();
    setUploading(true);
    setUploadPercent(0);

    try {
      const job = await createJob(
        file,
        { model, queries: queries || undefined },
        setUploadPercent
      );

      setUploading(false);
      setJobId(job.job_id);

      if (!job.queued) {
        toast.warning(
          "Stored, but no worker is listening. Start it with: python -m arq worker.WorkerSettings"
        );
      }
      if (job.reused_video) {
        toast.info("This file was already uploaded — reusing the stored copy.");
      }

      subscriptionRef.current = subscribeToJob(job.job_id, {
        onEvent: handleEvent,
        // Each frame renders the moment the worker announces it.
        onFrame: (frame) =>
          setFrames((current) =>
            current.some((f) => f.id === frame.id) ? current : [...current, frame]
          ),
        onSegment: (segment) =>
          setSegments((current) =>
            current.some((s) => s.id === segment.id) ? current : [...current, segment]
          ),
        onDone: async (event) => {
          if (event.event === "job.failed") {
            setFinished("failed");
            toast.error(event.message ?? "Analysis failed.");
            return;
          }
          setFinished("ok");
          setProgress(100);
          try {
            // Final pass: captions are written at the end of the pipeline.
            const snapshot = await getJob(job.job_id);
            setFrames(snapshot.frames);
            setScanId(snapshot.scan_id);
          } catch {
            /* the stream already told us it succeeded */
          }
        },
        onError: (text) => toast.error(text),
      });
    } catch (error) {
      setUploading(false);
      toast.error(errorMessage(error));
    }
  }

  return (
    <div className="space-y-6">
      {/* ---- picker ---- */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          pickFile(e.dataTransfer.files?.[0]);
        }}
        className={`rounded-xl border-2 border-dashed p-8 text-center transition-colors ${
          dragging ? "border-primary bg-primary/5" : "border-muted-foreground/25"
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          accept="video/*"
          className="hidden"
          onChange={(e) => pickFile(e.target.files?.[0] ?? undefined)}
        />
        <p className="text-sm text-muted-foreground">
          {file ? file.name : "Drop a video here, or"}
        </p>
        <Button
          variant="outline"
          className="mt-3"
          onClick={() => inputRef.current?.click()}
          disabled={running}
        >
          Choose a video
        </Button>
        {file && (
          <p className="mt-2 text-xs text-muted-foreground">
            {(file.size / (1024 * 1024)).toFixed(1)} MB
          </p>
        )}
      </div>

      {/* ---- model ---- */}
      <div>
        <p className="text-sm font-medium">Detection model</p>
        <div className="mt-2 flex flex-wrap gap-2">
          {MODELS.map((option) => (
            <button
              key={option.value}
              type="button"
              disabled={running}
              onClick={() => setModel(option.value)}
              title={option.hint}
              className={`rounded-lg border px-3 py-1.5 text-sm transition-colors disabled:opacity-50 ${
                model === option.value
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-muted-foreground/25 hover:bg-muted"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
        <p className="mt-1.5 text-xs text-muted-foreground">
          {MODELS.find((m) => m.value === model)?.hint}
        </p>
        {model === "owlv2" && (
          <input
            value={queries}
            onChange={(e) => setQueries(e.target.value)}
            disabled={running}
            placeholder="a gun, a knife, a backpack"
            className="mt-3 w-full rounded-lg border bg-background px-3 py-2 text-sm"
          />
        )}
      </div>

      <div className="flex gap-2">
        <Button onClick={start} disabled={!file || running}>
          {running ? "Analysing…" : "Analyse video"}
        </Button>
        {finished && (
          <>
            <Button variant="outline" onClick={reset}>
              Analyse another
            </Button>
            {scanId && (
              <Button variant="outline" onClick={() => router.push(`/report/${scanId}`)}>
                Open full report →
              </Button>
            )}
          </>
        )}
      </div>

      {/* ---- progress ---- */}
      {(running || finished) && (
        <div className="rounded-xl border p-4">
          <div className="flex items-center justify-between text-sm">
            <span className="font-medium">
              {uploading
                ? `Uploading… ${uploadPercent}%`
                : finished === "failed"
                  ? "Failed"
                  : finished === "ok"
                    ? "Analysis complete"
                    : stageLabel(stage)}
            </span>
            <span className="tabular-nums text-muted-foreground">
              {uploading ? uploadPercent : progress}%
            </span>
          </div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-muted">
            <div
              className={`h-full rounded-full transition-all duration-300 ${
                finished === "failed" ? "bg-destructive" : "bg-primary"
              }`}
              style={{ width: `${uploading ? uploadPercent : progress}%` }}
            />
          </div>
          {message && (
            <p className="mt-2 text-xs text-muted-foreground">{message}</p>
          )}
        </div>
      )}

      {/* ---- the video, with flagged spans filling in as they are found ---- */}
      {previewUrl && (
        <div className="rounded-xl border p-4">
          <video
            ref={previewRef}
            src={previewUrl}
            controls
            preload="metadata"
            onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
            onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
            className="mb-4 w-full rounded-lg border bg-black"
          />
          <IncidentTimeline
            duration={duration}
            currentTime={currentTime}
            incidents={segments.map((segment) => ({
              key: segment.id,
              start: segment.start_time,
              end: segment.end_time,
              label: segment.label,
              category: segment.category,
              confidence: segment.confidence,
            }))}
            onSeek={(second) => {
              if (previewRef.current) previewRef.current.currentTime = second;
              setCurrentTime(second);
            }}
          />
        </div>
      )}

      {/* ---- frames, appearing as the worker produces them ---- */}
      {frames.length > 0 && (
        <div>
          <h3 className="text-sm font-medium">
            Extracted frames{" "}
            <span className="text-muted-foreground">({frames.length})</span>
          </h3>
          <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {frames.map((frame) => (
              <figure
                key={frame.id}
                className="overflow-hidden rounded-lg border bg-muted/30"
              >
                {frame.url && (
                  /* eslint-disable-next-line @next/next/no-img-element --
                     presigned MinIO URLs are signed per object and expire, so
                     they cannot go through the build-time image optimiser. */
                  <img
                    src={frame.url}
                    alt={frame.caption ?? `Frame at ${formatTime(frame.captured_at_seconds)}`}
                    loading="lazy"
                    className="aspect-video w-full animate-in fade-in object-cover duration-500"
                  />
                )}
                <figcaption className="p-2">
                  <span className="font-mono text-[11px] text-muted-foreground">
                    {formatTime(frame.captured_at_seconds)}
                  </span>
                  {frame.caption && (
                    <p className="mt-0.5 line-clamp-2 text-xs">{frame.caption}</p>
                  )}
                </figcaption>
              </figure>
            ))}
          </div>
        </div>
      )}

      {/* ---- incidents, as they are found ---- */}
      {segments.length > 0 && (
        <div>
          <h3 className="text-sm font-medium">
            Incidents{" "}
            <span className="text-muted-foreground">({segments.length})</span>
          </h3>
          <div className="mt-3 space-y-3">
            {segments.map((segment) => (
              <div key={segment.id} className="rounded-xl border p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm text-muted-foreground">
                    {formatTime(segment.start_time)}–{formatTime(segment.end_time)}
                  </span>
                  <span className="font-medium">{segment.label}</span>
                  <span
                    className={`rounded-full border px-2 py-0.5 text-xs ${categoryClasses(
                      segment.category
                    )}`}
                  >
                    {segment.category}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {(segment.confidence * 100).toFixed(0)}%
                  </span>
                </div>
                {(segment.explanation || segment.description) && (
                  <p className="mt-1 text-sm text-muted-foreground">
                    {segment.explanation || segment.description}
                  </p>
                )}
                {segment.clip_url && (
                  <video
                    src={segment.clip_url}
                    controls
                    preload="metadata"
                    className="mt-3 w-full rounded-lg border bg-black sm:max-w-md"
                  />
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
