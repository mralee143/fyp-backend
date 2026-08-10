"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { errorMessage } from "@/lib/auth";
import { detectVideoAuto, validateVideo } from "@/lib/detection";
import { useScanStore } from "@/store/scan";

type Phase = "idle" | "uploading" | "analyzing";

export function VideoUpload() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    if (!previewUrl) return;
    return () => URL.revokeObjectURL(previewUrl);
  }, [previewUrl]);

  function pickFile(f: File | undefined) {
    if (!f) return;
    const err = validateVideo(f);
    if (err) {
      toast.error(err);
      return;
    }
    setFile(f);
    setPreviewUrl(URL.createObjectURL(f));
    setPhase("idle");
  }

  async function onScan() {
    if (!file) return;
    setProgress(0);
    setPhase("uploading");
    const onProg = (p: number) => {
      setProgress(p);
      if (p >= 100) setPhase("analyzing");
    };
    try {
      // Backend cascades through all models (Gemini → action → Qwen) and
      // returns the first that detects an incident.
      const data = await detectVideoAuto(file, onProg);
      useScanStore
        .getState()
        .setLlmScan(URL.createObjectURL(file), file.name, data);
      router.push("/results");
    } catch (err) {
      setPhase("idle");
      toast.error(errorMessage(err));
    }
  }

  const busy = phase !== "idle";

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

        {/* Preview of the picked clip */}
        {previewUrl && (
          <video
            src={previewUrl}
            controls
            preload="metadata"
            className="w-full rounded-xl border bg-black"
          />
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
                : "Analyzing with multiple AI models… usually under a minute, longer for clear clips. Keep this tab open."}
            </p>
          </div>
        )}

        <Button onClick={onScan} disabled={!file || busy} className="w-full">
          {busy ? "Working…" : "Run detection"}
        </Button>
      </CardContent>
    </Card>
  );
}
