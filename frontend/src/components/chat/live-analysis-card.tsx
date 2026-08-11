"use client";

import { CheckCircle2Icon, LoaderIcon, TriangleAlertIcon } from "lucide-react";

import { formatTime, stageLabel } from "@/lib/jobs";
import { useChatStore } from "@/store/chat";

/**
 * The streamed analysis, rendered inside the conversation.
 *
 * Same pipeline as the standalone analyser: the upload returns in about a
 * second and everything after that arrives by push, so extracted frames show up
 * well before the detection models finish. Clicking a frame or an incident
 * seeks the review player, which is what ties this to the rest of the chat.
 */

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
    default:
      return "border-zinc-500/40 bg-zinc-500/10 text-foreground";
  }
}

export function LiveAnalysisCard() {
  const live = useChatStore((s) => s.live);
  const command = useChatStore((s) => s.command);

  if (!live) return null;

  const running = live.status === "running";

  return (
    <div className="my-2 overflow-hidden rounded-xl border bg-muted/30">
      <div className="flex items-center gap-2 px-3 py-2 text-sm">
        {running ? (
          <LoaderIcon className="size-4 shrink-0 animate-spin text-primary" />
        ) : live.status === "done" ? (
          <CheckCircle2Icon className="size-4 shrink-0 text-green-600 dark:text-green-400" />
        ) : (
          <TriangleAlertIcon className="size-4 shrink-0 text-destructive" />
        )}
        <span className="font-medium">
          {running
            ? stageLabel(live.stage)
            : live.status === "done"
              ? "Analysis complete"
              : "Analysis failed"}
        </span>
        {running && (
          <span className="ms-auto font-mono text-xs text-muted-foreground">
            {live.progress}%
          </span>
        )}
      </div>

      {running && (
        <div className="h-1 w-full bg-muted">
          <div
            className="h-full bg-primary transition-all duration-500"
            style={{ width: `${live.progress}%` }}
          />
        </div>
      )}

      <div className="space-y-3 px-3 py-2.5">
        {live.message && (
          <p className="text-xs text-muted-foreground">{live.message}</p>
        )}

        {/* Frames, as the worker extracts them */}
        {live.frames.length > 0 && (
          <div>
            <p className="mb-1.5 text-xs font-medium text-muted-foreground">
              Frames ({live.frames.length})
            </p>
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
              {live.frames.map((frame) => (
                <button
                  key={frame.id}
                  type="button"
                  onClick={() =>
                    command({
                      action: "seek",
                      startTime: frame.captured_at_seconds ?? 0,
                    })
                  }
                  title={`Jump to ${formatTime(frame.captured_at_seconds)}`}
                  className={`overflow-hidden rounded-lg border bg-muted/50 text-start transition-opacity hover:opacity-90 ${
                    frame.segment_id ? "ring-2 ring-destructive/40" : ""
                  }`}
                >
                  {frame.url && (
                    /* eslint-disable-next-line @next/next/no-img-element --
                       presigned MinIO URLs expire, so they cannot be optimised
                       at build time. */
                    <img
                      src={frame.url}
                      alt={
                        frame.caption ??
                        `Frame at ${formatTime(frame.captured_at_seconds)}`
                      }
                      loading="lazy"
                      className="aspect-video w-full object-cover"
                    />
                  )}
                  <span className="block px-1.5 py-1 font-mono text-[11px] text-muted-foreground">
                    {formatTime(frame.captured_at_seconds)}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Incidents, as they are found */}
        {live.segments.length > 0 && (
          <div>
            <p className="mb-1.5 text-xs font-medium text-muted-foreground">
              Incidents ({live.segments.length})
            </p>
            <ul className="space-y-1.5">
              {live.segments.map((segment) => (
                <li key={segment.id}>
                  <button
                    type="button"
                    onClick={() =>
                      command({
                        action: "play_segment",
                        startTime: segment.start_time,
                        endTime: segment.end_time,
                      })
                    }
                    className="w-full rounded-lg border p-2 text-start transition-colors hover:bg-muted"
                  >
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-xs text-muted-foreground">
                        {formatTime(segment.start_time)}
                      </span>
                      <span className="text-sm font-medium">{segment.label}</span>
                      <span
                        className={`rounded-full border px-2 py-0.5 text-xs ${categoryClasses(segment.category)}`}
                      >
                        {segment.category}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {Math.round(segment.confidence * 100)}%
                      </span>
                    </span>
                    {segment.description && (
                      <span className="mt-0.5 block text-xs text-muted-foreground">
                        {segment.description}
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {live.status === "done" && live.segments.length === 0 && (
          <p className="text-xs text-muted-foreground">
            No incidents found. Ask me anything about the footage.
          </p>
        )}
      </div>
    </div>
  );
}
