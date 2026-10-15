"use client";

import { useMemo } from "react";
import { VideoOffIcon } from "lucide-react";

import {
  VideoReviewBox,
  type ReviewIncident,
  type ReviewTick,
} from "@/components/video-review-box";
import { mediaUrl } from "@/lib/detection";
import {
  DEFAULT_PANEL_WIDTH,
  WIDE_PANEL_WIDTH,
  useChatStore,
  useIsBusy,
  useLiveAnalysis,
} from "@/store/chat";

/**
 * The chat side of the review box: the attached video, wired to the agent.
 *
 * The box itself lives in `video-review-box.tsx` — the scan results page shows
 * the same one. This component only feeds it: it collects the incidents from
 * the conversation and the live job, routes every click through the chat store
 * (so "show me the gun" and a click on a chapter take one code path), and hands
 * the widen arrow to the panel around it.
 *
 * The caller keys this component on the video URL, so a new upload (or a switch
 * to another conversation) remounts it with a fresh player.
 */

/** Incidents from the agent's tool results — the stored, finished analysis. */
function fromMessages(
  messages: { role: string; tool_payload?: { result?: unknown } | null }[]
): ReviewIncident[] {
  const found: ReviewIncident[] = [];

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
): ReviewTick[] {
  const ticks: ReviewTick[] = [];
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

export function VideoPanel() {
  const detail = useChatStore((s) => s.detail);
  const live = useLiveAnalysis();
  const playback = useChatStore((s) => s.playback);
  const command = useChatStore((s) => s.command);
  const submit = useChatStore((s) => s.submit);
  const panelWidth = useChatStore((s) => s.panelWidth);
  const togglePanelWidth = useChatStore((s) => s.togglePanelWidth);
  const sending = useIsBusy();

  const src = mediaUrl(detail?.video_url);
  const messages = useMemo(() => detail?.messages ?? [], [detail?.messages]);
  const liveSegments = live?.segments;

  // Both sources describe the same incidents: the job streams them while the
  // analysis runs, the stored tool result carries them once it finishes. The
  // streamed one wins a tie — it is the one with the clip and the explanation.
  const incidents = useMemo<ReviewIncident[]>(() => {
    const byKey = new Map<string, ReviewIncident>();

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

  /** Ask the agent to talk one span through, in the conversation. */
  function explain(incident: ReviewIncident) {
    void submit(
      `Explain what happens between ${incident.start.toFixed(1)}s and ` +
        `${incident.end.toFixed(1)}s of the video — you flagged it as ` +
        `"${incident.label}". Describe who and what is involved.`
    ).catch(() => {});
  }

  return (
    <VideoReviewBox
      src={src}
      name={detail?.video_name}
      incidents={incidents}
      ticks={ticks}
      headerNote={
        live?.status === "running" ? `analysing… ${live.progress}%` : undefined
      }
      emptyNote={
        live?.status === "running"
          ? "Incidents appear here as the detectors find them."
          : "No incident sections — the whole clip came back clear."
      }
      emptyState={
        <div className="flex flex-col items-center gap-2 text-center">
          <VideoOffIcon className="size-6 text-muted-foreground" />
          <p className="text-sm font-medium">No video attached</p>
          <p className="text-xs text-muted-foreground">
            Attach one with the paperclip and it will play here. Once it has
            been analysed, the bar underneath marks every incident in its own
            colour.
          </p>
        </div>
      }
      playback={playback}
      onCommand={command}
      onExplain={explain}
      explainDisabled={sending}
      expanded={panelWidth > (DEFAULT_PANEL_WIDTH + WIDE_PANEL_WIDTH) / 2}
      onToggleExpanded={togglePanelWidth}
    />
  );
}
