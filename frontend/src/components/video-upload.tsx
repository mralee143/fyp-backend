"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { useScanStore } from "@/store/scan";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { errorMessage } from "@/lib/auth";
import {
  detectVideo,
  detectVideoLlm,
  detectVideoAction,
  validateVideo,
  MAX_LLM_BYTES,
  type VideoDetectionResponse,
  type LlmDetectionResponse,
  type ViolenceSegment,
} from "@/lib/detection";

type ModelKey = "yolo" | "owlv2" | "llm" | "action";

const MODELS: { value: ModelKey; title: string; hint: string }[] = [
  { value: "yolo", title: "Weapons (YOLO)", hint: "Fine-tuned: gun / knife / grenade" },
  { value: "owlv2", title: "Any object (OWLv2)", hint: "Zero-shot free-text queries" },
  {
    value: "action",
    title: "Fighting (local)",
    hint: "VideoMAE: fighting / assault / robbery — runs offline",
  },
  { value: "llm", title: "AI analysis (LLM)", hint: "Gemini: violence + harassment" },
];

/** Model paths that return described segments rather than object boxes. */
const SEGMENT_MODELS: ModelKey[] = ["llm", "action"];

type Phase = "idle" | "uploading" | "analyzing" | "done";

export function VideoUpload() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);

  // Local playback of the picked file — no upload round-trip needed.
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);

  const [model, setModel] = useState<ModelKey>("yolo");
  const [queries, setQueries] = useState("a gun, a knife, a person fighting");
  const [numFrames, setNumFrames] = useState(24);
  const [threshold, setThreshold] = useState(0.2);

  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<VideoDetectionResponse | null>(null);
  const [llmResult, setLlmResult] = useState<LlmDetectionResponse | null>(null);

  // Release the previous object URL whenever it changes or the view unmounts.
  useEffect(() => {
    if (!videoUrl) return;
    return () => URL.revokeObjectURL(videoUrl);
  }, [videoUrl]);

  function pickFile(f: File | undefined) {
    if (!f) return;
    const err = validateVideo(f);
    if (err) {
      toast.error(err);
      return;
    }
    setFile(f);
    setVideoUrl(URL.createObjectURL(f));
    setDuration(0);
    setCurrentTime(0);
    setResult(null);
    setLlmResult(null);
    setPhase("idle");
  }

  /** Jump the player to a moment and play it. */
  function seekTo(seconds: number) {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = seconds;
    // Autoplay can be blocked; seeking still works, so ignore the rejection.
    void v.play().catch(() => {});
    v.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async function onScan() {
    if (!file) return;
    if (model === "llm" && file.size > MAX_LLM_BYTES) {
      toast.error(
        `For AI analysis, use a clip under ${MAX_LLM_BYTES / (1024 * 1024)} MB.`
      );
      return;
    }
    setResult(null);
    setLlmResult(null);
    setProgress(0);
    setPhase("uploading");
    const onProg = (p: number) => {
      setProgress(p);
      if (p >= 100) setPhase("analyzing");
    };
    try {
      if (model === "llm" || model === "action") {
        const data =
          model === "llm"
            ? await detectVideoLlm(file, onProg)
            : await detectVideoAction(file, onProg);
        setLlmResult(data);
        setPhase("done");
        toast.success(
          data.violence_detected
            ? "Violence detected in the video."
            : "Analysis complete — no violence found."
        );
        // Carry the clip + result to the dedicated results page.
        useScanStore.getState().setLlmScan(URL.createObjectURL(file), file.name, data);
        router.push("/results");
      } else {
        const data = await detectVideo(
          file,
          { model, queries, numFrames, threshold },
          onProg
        );
        setResult(data);
        setPhase("done");
        toast.success(
          data.weapon_detected
            ? "Threat detected in the video."
            : "Scan complete — no threats found."
        );
        useScanStore.getState().setObjectScan(URL.createObjectURL(file), file.name, data);
        router.push("/results");
      }
    } catch (err) {
      setPhase("idle");
      toast.error(errorMessage(err));
    }
  }

  const busy = phase === "uploading" || phase === "analyzing";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Analyze a video</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Drop zone */}
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
          onClick={() => inputRef.current?.click()}
          className={`grid cursor-pointer place-items-center rounded-xl border border-dashed py-12 text-center transition-colors ${
            dragging ? "border-primary bg-muted" : "hover:bg-muted/50"
          }`}
        >
          <input
            ref={inputRef}
            type="file"
            accept="video/*"
            className="hidden"
            onChange={(e) => pickFile(e.target.files?.[0] ?? undefined)}
          />
          <p className="font-medium">
            {file ? file.name : "Drag & drop a video, or click to browse"}
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            {file
              ? `${(file.size / (1024 * 1024)).toFixed(1)} MB`
              : "MP4/MOV/AVI/MKV/WEBM · max 200 MB"}
          </p>
        </div>

        {/* Player — the clip itself, so results can jump into it */}
        {videoUrl && (
          <video
            ref={videoRef}
            src={videoUrl}
            controls
            preload="metadata"
            className="w-full rounded-xl border bg-black"
            onLoadedMetadata={(e) => setDuration(e.currentTarget.duration || 0)}
            onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
          />
        )}

        {/* Model selector */}
        <div className="grid gap-3 sm:grid-cols-2">
          {MODELS.map((m) => (
            <button
              key={m.value}
              type="button"
              onClick={() => setModel(m.value)}
              className={`rounded-xl border p-4 text-left transition-colors ${
                model === m.value
                  ? "border-primary bg-muted"
                  : "hover:bg-muted/50"
              }`}
            >
              <div className="font-medium">{m.title}</div>
              <div className="text-sm text-muted-foreground">{m.hint}</div>
            </button>
          ))}
        </div>

        {model === "owlv2" && (
          <div className="space-y-2">
            <Label htmlFor="queries">What to look for (comma-separated)</Label>
            <Input
              id="queries"
              value={queries}
              onChange={(e) => setQueries(e.target.value)}
              placeholder="a gun, a knife, a person fighting"
            />
          </div>
        )}

        {/* Params (object detectors only) */}
        {!SEGMENT_MODELS.includes(model) && (
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="frames">Frames to sample: {numFrames}</Label>
            <input
              id="frames"
              type="range"
              min={4}
              max={64}
              step={1}
              value={numFrames}
              onChange={(e) => setNumFrames(Number(e.target.value))}
              className="w-full"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="threshold">
              Confidence threshold: {threshold.toFixed(2)}
            </Label>
            <input
              id="threshold"
              type="range"
              min={0.05}
              max={0.9}
              step={0.05}
              value={threshold}
              onChange={(e) => setThreshold(Number(e.target.value))}
              className="w-full"
            />
          </div>
        </div>
        )}

        {model === "llm" && (
          <p className="text-sm text-muted-foreground">
            AI analysis reads the whole clip and describes violence, theft and
            harassment with timestamps. Needs a Gemini API key. Use a clip under{" "}
            {MAX_LLM_BYTES / (1024 * 1024)} MB.
          </p>
        )}

        {model === "action" && (
          <p className="text-sm text-muted-foreground">
            Recognises actions over time — fighting, assault, abuse, robbery,
            shooting. Runs locally with no API key. It cannot detect harassment;
            use AI analysis for that.
          </p>
        )}

        {/* Progress */}
        {busy && (
          <div className="space-y-2">
            <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: `${phase === "analyzing" ? 100 : progress}%` }}
              />
            </div>
            <p className="text-sm text-muted-foreground">
              {phase === "uploading"
                ? `Uploading… ${progress}%`
                : "Analyzing video… this can take a moment."}
            </p>
          </div>
        )}

        <Button onClick={onScan} disabled={!file || busy} className="w-full">
          {busy ? "Working…" : "Run detection"}
        </Button>

        {/* Inline result summary (full results view is Phase 9) */}
        {result && <ResultSummary result={result} onSeek={seekTo} />}
        {llmResult && (
          <LlmResultSummary
            result={llmResult}
            duration={duration}
            currentTime={currentTime}
            onSeek={seekTo}
          />
        )}
      </CardContent>
    </Card>
  );
}

function fmtTime(s: number): string {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

/**
 * Violence segments laid out over the clip's duration. Needs a known duration
 * to place markers, so it renders nothing until the player reports one.
 */
function ViolenceTimeline({
  segments,
  duration,
  currentTime,
  onSeek,
}: {
  segments: ViolenceSegment[];
  duration: number;
  currentTime: number;
  onSeek: (s: number) => void;
}) {
  if (duration <= 0 || segments.length === 0) return null;

  const pct = (t: number) => Math.min(100, Math.max(0, (t / duration) * 100));

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>Timeline — click a marker to jump</span>
        <span className="font-mono">
          {fmtTime(currentTime)} / {fmtTime(duration)}
        </span>
      </div>

      <div className="relative h-8 w-full overflow-hidden rounded-lg border bg-muted">
        {segments.map((s, i) => {
          const left = pct(s.start_time);
          // Keep very short events clickable rather than sub-pixel slivers.
          const width = Math.max(1, pct(s.end_time) - left);
          return (
            <button
              key={i}
              type="button"
              onClick={() => onSeek(s.start_time)}
              title={`${s.label} · ${fmtTime(s.start_time)}–${fmtTime(s.end_time)}`}
              aria-label={`Jump to ${s.label} at ${fmtTime(s.start_time)}`}
              className="absolute inset-y-0 bg-destructive/70 transition-colors hover:bg-destructive"
              style={{ left: `${left}%`, width: `${width}%` }}
            />
          );
        })}

        {/* Playhead */}
        <div
          className="pointer-events-none absolute inset-y-0 w-0.5 bg-foreground"
          style={{ left: `${pct(currentTime)}%` }}
        />
      </div>
    </div>
  );
}

function LlmResultSummary({
  result,
  duration,
  currentTime,
  onSeek,
}: {
  result: LlmDetectionResponse;
  duration: number;
  currentTime: number;
  onSeek: (s: number) => void;
}) {
  return (
    <div className="space-y-4">
      <div
        className={`rounded-xl border p-4 ${
          result.violence_detected
            ? "border-destructive/40 bg-destructive/10"
            : "border-green-500/40 bg-green-500/10"
        }`}
      >
        <p className="font-semibold">
          {result.violence_detected
            ? "⚠️ Violence detected"
            : "✅ No violence found"}
        </p>
        {result.summary && (
          <p className="mt-1 text-sm text-muted-foreground">{result.summary}</p>
        )}
        <p className="mt-1 text-xs text-muted-foreground">model {result.model_id}</p>
      </div>

      <ViolenceTimeline
        segments={result.segments}
        duration={duration}
        currentTime={currentTime}
        onSeek={onSeek}
      />

      {result.segments.length > 0 && (
        <div className="overflow-x-auto rounded-xl border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left">
              <tr>
                <th className="px-3 py-2">Time</th>
                <th className="px-3 py-2">Event</th>
                <th className="px-3 py-2">Description</th>
                <th className="px-3 py-2">Conf.</th>
              </tr>
            </thead>
            <tbody>
              {result.segments.map((s, i) => {
                const playing =
                  currentTime >= s.start_time && currentTime <= s.end_time;
                return (
                  <tr
                    key={i}
                    onClick={() => onSeek(s.start_time)}
                    className={`cursor-pointer border-t align-top transition-colors hover:bg-muted/50 ${
                      playing ? "bg-destructive/10" : ""
                    }`}
                  >
                    <td className="whitespace-nowrap px-3 py-2 font-mono text-primary underline-offset-2 hover:underline">
                      {fmtTime(s.start_time)}–{fmtTime(s.end_time)}
                    </td>
                    <td className="px-3 py-2 font-medium">
                      {s.label}
                      {s.category && (
                        <span className="ml-2 rounded-full border px-2 py-0.5 text-xs font-normal text-muted-foreground">
                          {s.category}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">
                      {s.description}
                    </td>
                    <td className="px-3 py-2">{(s.confidence * 100).toFixed(0)}%</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ResultSummary({
  result,
  onSeek,
}: {
  result: VideoDetectionResponse;
  onSeek: (s: number) => void;
}) {
  return (
    <div className="space-y-4">
      <div
        className={`rounded-xl border p-4 ${
          result.weapon_detected
            ? "border-destructive/40 bg-destructive/10"
            : "border-green-500/40 bg-green-500/10"
        }`}
      >
        <p className="font-semibold">
          {result.weapon_detected
            ? "⚠️ Threat detected"
            : "✅ No threats found"}
        </p>
        <p className="text-sm text-muted-foreground">
          {result.detection_count} detection(s) across {result.frames_scanned}{" "}
          sampled frames · model {result.model_id}
        </p>
      </div>

      {result.detections.length > 0 && (
        <div className="overflow-x-auto rounded-xl border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left">
              <tr>
                <th className="px-3 py-2">Time</th>
                <th className="px-3 py-2">Label</th>
                <th className="px-3 py-2">Score</th>
              </tr>
            </thead>
            <tbody>
              {result.detections.map((d, i) => (
                <tr
                  key={i}
                  onClick={() => onSeek(d.second)}
                  className="cursor-pointer border-t transition-colors hover:bg-muted/50"
                >
                  <td className="px-3 py-2 font-mono text-primary underline-offset-2 hover:underline">
                    {d.timestamp}
                  </td>
                  <td className="px-3 py-2">{d.label}</td>
                  <td className="px-3 py-2">{(d.score * 100).toFixed(0)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
