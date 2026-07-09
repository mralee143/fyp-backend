"use client";

import { useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { errorMessage } from "@/lib/auth";
import {
  detectVideo,
  validateVideo,
  type VideoDetectionResponse,
  type DetectOptions,
} from "@/lib/detection";

const MODELS: { value: DetectOptions["model"]; title: string; hint: string }[] = [
  { value: "yolo", title: "Weapons (YOLO)", hint: "Fine-tuned: gun / knife / grenade" },
  { value: "owlv2", title: "Any object (OWLv2)", hint: "Zero-shot free-text queries" },
];

type Phase = "idle" | "uploading" | "analyzing" | "done";

export function VideoUpload() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);

  const [model, setModel] = useState<DetectOptions["model"]>("yolo");
  const [queries, setQueries] = useState("a gun, a knife, a person fighting");
  const [numFrames, setNumFrames] = useState(24);
  const [threshold, setThreshold] = useState(0.2);

  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<VideoDetectionResponse | null>(null);

  function pickFile(f: File | undefined) {
    if (!f) return;
    const err = validateVideo(f);
    if (err) {
      toast.error(err);
      return;
    }
    setFile(f);
    setResult(null);
    setPhase("idle");
  }

  async function onScan() {
    if (!file) return;
    setResult(null);
    setProgress(0);
    setPhase("uploading");
    try {
      const data = await detectVideo(
        file,
        { model, queries, numFrames, threshold },
        (p) => {
          setProgress(p);
          if (p >= 100) setPhase("analyzing");
        }
      );
      setResult(data);
      setPhase("done");
      toast.success(
        data.weapon_detected
          ? "Threat detected in the video."
          : "Scan complete — no threats found."
      );
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

        {/* Params */}
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
        {result && <ResultSummary result={result} />}
      </CardContent>
    </Card>
  );
}

function ResultSummary({ result }: { result: VideoDetectionResponse }) {
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
                <tr key={i} className="border-t">
                  <td className="px-3 py-2 font-mono">{d.timestamp}</td>
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
