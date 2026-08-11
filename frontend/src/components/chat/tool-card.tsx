"use client";

import { useState } from "react";

import { TOOL_LABELS, type ToolResult } from "@/lib/chat";
import { mediaUrl } from "@/lib/detection";
import { useChatStore } from "@/store/chat";

/**
 * Renders one tool the agent ran, so the conversation shows its work rather
 * than an unsourced claim: which detector scanned the video, what SQL the
 * database agent wrote, and the rows either produced.
 */

interface ToolCardProps {
  name: string;
  args?: Record<string, unknown>;
  result: ToolResult;
}

function Chip({ children, tone = "default" }: { children: React.ReactNode; tone?: "default" | "danger" | "ok" }) {
  const tones = {
    default: "bg-muted text-muted-foreground",
    danger: "bg-destructive/10 text-destructive",
    ok: "bg-green-500/10 text-green-600 dark:text-green-400",
  };
  return (
    <span className={`inline-flex items-center gap-x-1 py-0.5 px-2 rounded-full text-xs font-medium ${tones[tone]}`}>
      {children}
    </span>
  );
}

/**
 * A captured still. Clicking it seeks the review player to that moment, so the
 * picture and the video stay tied together.
 */
function FrameShot({
  url,
  second,
  caption,
  onSeek,
}: {
  url: string;
  second?: number;
  caption?: string;
  onSeek: () => void;
}) {
  return (
    <figure className="overflow-hidden rounded-lg border bg-muted/30">
      <button
        type="button"
        onClick={onSeek}
        title={second !== undefined ? `Jump to ${second.toFixed(1)}s` : "Jump to this moment"}
        className="block w-full"
      >
        {/* eslint-disable-next-line @next/next/no-img-element --
            frames are written at request time under /media, so there is
            nothing for the image optimiser to prebuild. */}
        <img
          src={mediaUrl(url)}
          alt={caption ?? `Frame at ${second?.toFixed(1) ?? "?"}s`}
          loading="lazy"
          className="aspect-video w-full object-cover transition-opacity hover:opacity-90"
        />
      </button>
      {caption && (
        <figcaption className="px-2 py-1.5 text-xs text-muted-foreground">
          {caption}
        </figcaption>
      )}
    </figure>
  );
}

export function ToolCard({ name, args, result }: ToolCardProps) {
  // Evidence should not need a click. If the result carries a picture — or
  // failed and needs explaining — the card opens itself; chattier results
  // (a playback directive, a scan list) stay folded away.
  const hasEvidence = Boolean(
    result.frame_url ||
      result.error ||
      result.top_detections?.some((d) => d.frame_url) ||
      result.segments?.some((s) => s.frame_url)
  );
  const [open, setOpen] = useState(hasEvidence);
  // Timestamps in a result are clickable: they drive the same player the agent
  // controls, so "show me 00:12" and clicking 00:12 do the same thing.
  const command = useChatStore((s) => s.command);

  const label = TOOL_LABELS[name] ?? name;
  const threat = Boolean(result.weapon_detected || result.violence_detected);

  return (
    <div className="my-2 rounded-xl border bg-muted/30 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-x-2 py-2 px-3 text-start text-sm text-foreground hover:bg-muted/60 focus:outline-hidden"
      >
        <svg
          className="shrink-0 size-4 text-primary"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
        </svg>
        <span className="font-medium">{label}</span>

        {name === "analyze_video" && !result.error && (
          <Chip tone={threat ? "danger" : "ok"}>{threat ? "Threat found" : "Clear"}</Chip>
        )}
        {result.detector && <Chip>{result.detector}</Chip>}
        {typeof result.row_count === "number" && <Chip>{result.row_count} rows</Chip>}
        {result.error && <Chip tone="danger">failed</Chip>}

        <svg
          className={`ms-auto shrink-0 size-4 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>

      {open && (
        <div className="px-3 pb-3 pt-1 text-sm text-muted-foreground space-y-3">
          {result.error && <p className="text-destructive">{result.error}</p>}

          {/* A single captured still (show_frame_at) */}
          {result.kind === "frame" && result.frame_url && (
            <FrameShot
              url={result.frame_url}
              second={result.second}
              caption={[result.timestamp, result.note, result.location]
                .filter(Boolean)
                .join(" · ")}
              onSeek={() => command({ action: "seek", startTime: result.second ?? 0 })}
            />
          )}

          {/* Playback directive — replay it on demand */}
          {result.kind === "playback" && (
            <>
              <p className="text-xs">
                Player: <span className="font-medium text-foreground">{result.action}</span>
                {typeof result.start_time === "number" && ` from ${result.start_time.toFixed(0)}s`}
                {typeof result.end_time === "number" && ` to ${result.end_time.toFixed(0)}s`}
              </p>
              {result.reason && <p>{result.reason}</p>}
              <button
                type="button"
                onClick={() =>
                  command({
                    action: result.action ?? "play",
                    startTime: result.start_time ?? undefined,
                    endTime: result.end_time ?? undefined,
                    reason: result.reason,
                  })
                }
                className="rounded-lg border px-2 py-1 text-xs transition-colors hover:bg-muted"
              >
                Show me again
              </button>
            </>
          )}

          {/* Object detection (YOLO / OWLv2) */}
          {result.kind === "object_detection" && (
            <>
              <p className="text-xs">
                {result.detection_count ?? 0} detection(s) across {result.frames_scanned ?? 0} sampled
                frames · model <span className="font-mono">{result.model_id}</span>
              </p>

              {result.label_counts && Object.keys(result.label_counts).length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(result.label_counts).map(([lbl, count]) => (
                    <Chip key={lbl} tone="danger">
                      {lbl} × {count}
                    </Chip>
                  ))}
                </div>
              )}

              {/* The exact frames the detector fired on */}
              {result.top_detections?.some((d) => d.frame_url) && (
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {result.top_detections
                    .filter((d) => d.frame_url)
                    .map((d, i) => (
                      <FrameShot
                        key={`shot-${d.second}-${i}`}
                        url={d.frame_url as string}
                        second={d.second}
                        caption={`${d.timestamp} · ${d.label} ${Math.round(d.score * 100)}%${d.location ? ` · ${d.location}` : ""}`}
                        onSeek={() => command({ action: "seek", startTime: d.second })}
                      />
                    ))}
                </div>
              )}

              {result.top_detections && result.top_detections.length > 0 && (
                <ul className="space-y-1">
                  {result.top_detections.map((d, i) => (
                    <li key={`${d.timestamp}-${i}`}>
                      <button
                        type="button"
                        onClick={() => command({ action: "seek", startTime: d.second })}
                        title={`Jump to ${d.timestamp}`}
                        className="flex w-full items-center gap-x-2 rounded-lg px-1 py-0.5 text-start transition-colors hover:bg-muted"
                      >
                        <span className="font-mono text-xs text-primary underline-offset-2 hover:underline">
                          {d.timestamp}
                        </span>
                        <span className="text-foreground">{d.label}</span>
                        <span className="text-xs">{Math.round(d.score * 100)}%</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {result.truncated && <p className="text-xs italic">Showing the strongest matches only.</p>}
            </>
          )}

          {/* Incident analysis (action / VLM / auto) */}
          {result.kind === "incident_analysis" && (
            <>
              {result.summary && <p>{result.summary}</p>}
              {result.segments && result.segments.length > 0 && (
                <ul className="space-y-2">
                  {result.segments.map((s, i) => (
                    <li
                      key={`${s.label}-${i}`}
                      className="border-s-2 border-destructive/50 ps-3 py-0.5"
                    >
                      <div className="flex items-center gap-x-2">
                        <span className="font-medium text-foreground">
                          {s.label}
                        </span>
                        <Chip>{s.category}</Chip>
                        <button
                          type="button"
                          onClick={() =>
                            command({
                              action: "play_segment",
                              startTime: s.start_time,
                              endTime: s.end_time,
                            })
                          }
                          title="Play this moment"
                          className="font-mono text-xs text-primary underline-offset-2 hover:underline"
                        >
                          {s.start_time.toFixed(0)}s – {s.end_time.toFixed(0)}s
                        </button>
                        <span className="text-xs">{Math.round(s.confidence * 100)}%</span>
                      </div>
                      {s.description && <p className="text-xs mt-0.5">{s.description}</p>}
                      {s.frame_url && (
                        <div className="mt-2 max-w-xs">
                          <FrameShot
                            url={s.frame_url}
                            second={s.frame_second ?? s.start_time}
                            caption={`Frame at ${(s.frame_second ?? s.start_time).toFixed(1)}s${s.location ? ` · ${s.location}` : ""}`}
                            onSeek={() =>
                              command({
                                action: "play_segment",
                                startTime: s.start_time,
                                endTime: s.end_time,
                              })
                            }
                          />
                        </div>
                      )}
                      {s.clip_url && (
                        <video
                          src={mediaUrl(s.clip_url)}
                          controls
                          className="mt-2 max-w-xs rounded-lg border border-border"
                        />
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}

          {/* Database agent */}
          {result.sql && (
            <>
              {result.insight && (
                <p className="text-foreground">{result.insight}</p>
              )}
              <details className="group">
                <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                  SQL the analyst agent wrote
                </summary>
                <pre className="mt-1 p-2 rounded-lg bg-muted text-xs overflow-x-auto whitespace-pre-wrap">
                  {result.sql}
                </pre>
              </details>

              {result.rows && result.rows.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="min-w-full text-xs">
                    <thead>
                      <tr className="text-start text-muted-foreground">
                        {Object.keys(result.rows[0]).map((col) => (
                          <th key={col} className="py-1 pe-3 text-start font-medium">
                            {col}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {result.rows.slice(0, 10).map((row, i) => (
                        <tr key={i} className="border-t border-border">
                          {Object.values(row).map((cell, j) => (
                            <td key={j} className="py-1 pe-3 align-top">
                              {String(cell)}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {result.rows.length > 10 && (
                    <p className="mt-1 text-xs italic">
                      Showing 10 of {result.row_count} rows.
                    </p>
                  )}
                </div>
              )}
            </>
          )}

          {/* Scan list */}
          {result.scans && (
            <ul className="space-y-1">
              {result.scans.map((scan, i) => (
                <li key={i} className="flex items-center gap-x-2">
                  <span className="font-mono text-xs">#{String(scan.id)}</span>
                  <span className="truncate text-foreground">
                    {String(scan.filename)}
                  </span>
                  <Chip tone={scan.violence_detected ? "danger" : "ok"}>
                    {scan.violence_detected ? "threat" : "clear"}
                  </Chip>
                </li>
              ))}
            </ul>
          )}

          {args && Object.keys(args).length > 0 && (
            <p className="text-xs text-muted-foreground font-mono">
              {JSON.stringify(args)}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
