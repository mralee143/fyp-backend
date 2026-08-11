"use client";

import { useRef, useState } from "react";

import { formatTime } from "@/lib/jobs";

/**
 * The whole video as one bar, with every flagged span painted on it.
 *
 * A list of incidents tells you a fight was found; this tells you *where* — a
 * 60-second video with trouble from 0:50 to 0:55 shows five seconds of red near
 * the right-hand end, and the eleven quiet seconds either side stay grey. That
 * shape is the thing an operator reads first, so it is the thing the report
 * leads with.
 *
 * Clicking anywhere seeks; clicking a band also selects that incident, which is
 * what hands the clip to the chat agent.
 */

export interface TimelineIncident {
  key: number;
  start: number;
  end: number;
  label: string;
  category: string;
  confidence: number;
}

/** Solid fill per category — the muted card colours are unreadable at 8px tall. */
function categoryFill(category: string): string {
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

/** Evenly spaced marks that stay legible — six at most, on whole seconds. */
function ticks(duration: number): number[] {
  const count = Math.min(6, Math.max(2, Math.round(duration / 10)));
  const step = duration / count;
  return Array.from({ length: count + 1 }, (_, index) => index * step);
}

export function IncidentTimeline({
  duration,
  incidents,
  currentTime = 0,
  selectedKey = null,
  onSelect,
  onSeek,
}: {
  duration: number;
  incidents: TimelineIncident[];
  currentTime?: number;
  selectedKey?: number | null;
  onSelect?: (incident: TimelineIncident) => void;
  onSeek?: (second: number) => void;
}) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = useState<TimelineIncident | null>(null);

  // A zero duration would divide every band to Infinity; fall back to the last
  // incident so a video whose probe failed still draws something truthful.
  const span =
    duration > 0
      ? duration
      : incidents.reduce((longest, i) => Math.max(longest, i.end), 0) || 1;

  const percent = (second: number) =>
    Math.min(100, Math.max(0, (second / span) * 100));

  function seekFromClick(event: React.MouseEvent<HTMLDivElement>) {
    if (!onSeek || !trackRef.current) return;
    const bounds = trackRef.current.getBoundingClientRect();
    const ratio = (event.clientX - bounds.left) / bounds.width;
    onSeek(Math.min(span, Math.max(0, ratio * span)));
  }

  const flagged = incidents.reduce((total, i) => total + (i.end - i.start), 0);

  return (
    <div>
      <div className="flex items-baseline justify-between text-xs text-muted-foreground">
        <span>
          {incidents.length > 0
            ? `${incidents.length} flagged section${incidents.length > 1 ? "s" : ""} · ${flagged.toFixed(1)}s of ${formatTime(span)}`
            : `Nothing flagged in ${formatTime(span)}`}
        </span>
        {hovered && (
          <span className="font-mono text-foreground">
            {hovered.label} · {formatTime(hovered.start)}–{formatTime(hovered.end)} ·{" "}
            {(hovered.confidence * 100).toFixed(0)}%
          </span>
        )}
      </div>

      <div
        ref={trackRef}
        onClick={seekFromClick}
        className="relative mt-2 h-9 w-full cursor-pointer overflow-hidden rounded-lg border bg-muted"
      >
        {incidents.map((incident) => {
          const left = percent(incident.start);
          const width = Math.max(percent(incident.end) - left, 0.8);
          const selected = selectedKey === incident.key;
          return (
            <button
              key={incident.key}
              type="button"
              title={`${incident.label} · ${formatTime(incident.start)}–${formatTime(
                incident.end
              )} · ${(incident.confidence * 100).toFixed(0)}%`}
              aria-label={`${incident.label} from ${formatTime(
                incident.start
              )} to ${formatTime(incident.end)}`}
              onMouseEnter={() => setHovered(incident)}
              onMouseLeave={() => setHovered(null)}
              onClick={(event) => {
                // The track seeks on click; a band means "this one", so it
                // selects and seeks to the incident's own start instead.
                event.stopPropagation();
                onSelect?.(incident);
                onSeek?.(incident.start);
              }}
              style={{ left: `${left}%`, width: `${width}%` }}
              className={`absolute inset-y-0 min-w-[3px] transition-opacity ${categoryFill(
                incident.category
              )} ${
                selected
                  ? "opacity-100 ring-2 ring-foreground ring-inset"
                  : "opacity-80 hover:opacity-100"
              }`}
            />
          );
        })}

        {currentTime > 0 && (
          <div
            aria-hidden
            style={{ left: `${percent(currentTime)}%` }}
            className="pointer-events-none absolute inset-y-0 w-0.5 bg-foreground"
          />
        )}
      </div>

      <div className="relative mt-1 h-4">
        {ticks(span).map((second, index, all) => (
          <span
            key={second}
            style={{ left: `${percent(second)}%` }}
            className={`absolute font-mono text-[10px] text-muted-foreground ${
              index === 0
                ? ""
                : index === all.length - 1
                  ? "-translate-x-full"
                  : "-translate-x-1/2"
            }`}
          >
            {formatTime(second)}
          </span>
        ))}
      </div>
    </div>
  );
}
