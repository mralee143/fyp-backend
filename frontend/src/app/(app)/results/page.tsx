"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import {
  VideoReviewBox,
  type ReviewIncident,
} from "@/components/video-review-box";
import { useScanStore } from "@/store/scan";
import { mediaUrl } from "@/lib/detection";

/**
 * The scan result: the verdict, then the very same review box the chat uses.
 *
 * There is one review UI in the app (`video-review-box.tsx`) — the player, the
 * chapter bar over the runtime, and the chapter list that unfolds each
 * incident's clip and explanation. A scan opened here therefore reads exactly
 * the way it does in the chat panel, instead of a second, thinner timeline that
 * has to be learned separately.
 */

export default function ResultsPage() {
  const router = useRouter();

  const videoUrl = useScanStore((s) => s.videoUrl);
  const videoName = useScanStore((s) => s.videoName);
  const objectResult = useScanStore((s) => s.objectResult);
  const llmResult = useScanStore((s) => s.llmResult);

  // The (app) layout handles auth; this page additionally needs a scan loaded.
  useEffect(() => {
    if (!useScanStore.getState().videoUrl) {
      router.replace("/dashboard");
    }
  }, [router]);

  if (!videoUrl) return null;

  // Both result shapes become the same incident list the box renders.
  let incidents: ReviewIncident[] = [];
  let detected = false;
  let summary = "";
  let modelId = "";

  if (llmResult) {
    detected = llmResult.violence_detected;
    summary = llmResult.summary;
    modelId = llmResult.model_id;
    incidents = llmResult.segments.map((s, i) => ({
      key: `segment-${i}-${s.start_time}`,
      start: s.start_time,
      end: s.end_time,
      label: s.label,
      category: s.category || "violence",
      description: s.description,
      explanation: s.explanation,
      clipUrl: mediaUrl(s.clip_url),
      confidence: s.confidence,
    }));
  } else if (objectResult) {
    detected = objectResult.weapon_detected;
    modelId = objectResult.model_id;
    summary = `${objectResult.detection_count} detection(s) across ${objectResult.frames_scanned} sampled frames.`;
    // Object hits are instants; the box gives each one a clickable sliver.
    incidents = objectResult.detections.map((d, i) => ({
      key: `detection-${i}-${d.second}`,
      start: d.second,
      end: d.second,
      label: d.label,
      category: "violence",
      description: `Detected ${d.label}`,
      confidence: d.score,
    }));
  }

  return (
    <div className="mx-auto w-full max-w-5xl px-6 py-8">
      {videoName && (
        <p className="mb-4 truncate text-sm text-muted-foreground">{videoName}</p>
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
        {summary && <p className="mt-1 text-sm text-muted-foreground">{summary}</p>}
        <p className="mt-1 text-xs text-muted-foreground">model {modelId}</p>
      </div>

      {/* The review box — same player, chapter bar and incident list as chat.
          Keyed on the source so a fresh scan gets a reset player. */}
      <div className="mt-6 overflow-hidden rounded-xl border bg-card">
        <VideoReviewBox
          key={videoUrl}
          src={videoUrl}
          name={videoName}
          incidents={incidents}
          title="Video review"
          headerNote={
            incidents.length > 0
              ? `${incidents.length} flagged section${incidents.length > 1 ? "s" : ""}`
              : undefined
          }
          emptyNote="No incident sections — the whole clip came back clear."
        />
      </div>
    </div>
  );
}
