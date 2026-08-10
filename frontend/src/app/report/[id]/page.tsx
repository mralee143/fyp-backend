"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { useAuthStore } from "@/store/auth";
import { errorMessage } from "@/lib/auth";
import { getScan, mediaUrl, type ScanDetail } from "@/lib/detection";

function fmtTime(s: number): string {
  if (!isFinite(s)) return "0:00";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

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

interface Moment {
  start: number;
  end: number;
  label: string;
  category: string;
  text: string;
  confidence: number;
  clipUrl?: string;
}

export default function ReportPage() {
  const router = useRouter();
  const params = useParams();
  const id = Number(params?.id);

  const [scan, setScan] = useState<ScanDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!useAuthStore.getState().token) {
      router.replace("/login");
      return;
    }
    setReady(true);
    getScan(id)
      .then(setScan)
      .catch((e) => setError(errorMessage(e)));
  }, [id, router]);

  if (!ready) return null;

  if (error) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-12">
        <p className="text-destructive">{error}</p>
        <Button
          render={<Link href="/dashboard" />}
          nativeButton={false}
          variant="outline"
          className="mt-4"
        >
          ← Back to dashboard
        </Button>
      </div>
    );
  }

  if (!scan) {
    return <div className="px-6 py-12 text-muted-foreground">Loading report…</div>;
  }

  const detected = scan.violence_detected;
  const segments = scan.result?.segments ?? [];
  const detections = scan.result?.detections ?? [];

  const moments: Moment[] = segments.length
    ? segments.map((s) => ({
        start: s.start_time,
        end: s.end_time,
        label: s.label,
        category: s.category || "violence",
        text: s.explanation || s.description,
        confidence: s.confidence,
        clipUrl: mediaUrl(s.clip_url),
      }))
    : detections.map((d) => ({
        start: d.second,
        end: d.second,
        label: d.label,
        category: "violence",
        text: `Detected ${d.label}`,
        confidence: d.score,
      }));

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-2 font-semibold">
            <span className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground">
              S
            </span>
            Detection report
          </div>
          <Button
            render={<Link href="/dashboard" />}
            nativeButton={false}
            variant="outline"
            size="sm"
          >
            ← Dashboard
          </Button>
        </div>
      </header>

      <main className="mx-auto w-full max-w-4xl flex-1 px-6 py-8">
        <p className="text-sm text-muted-foreground">
          {scan.filename} · {scan.model} · {new Date(scan.created_at).toLocaleString()}
        </p>

        <div
          className={`mt-4 rounded-xl border p-4 ${
            detected
              ? "border-destructive/40 bg-destructive/10"
              : "border-green-500/40 bg-green-500/10"
          }`}
        >
          <p className="text-lg font-semibold">
            {detected ? "⚠️ Incident detected" : "✅ No incident found"}
          </p>
          {scan.summary && (
            <p className="mt-1 text-sm text-muted-foreground">{scan.summary}</p>
          )}
        </div>

        <h2 className="mt-8 text-lg font-semibold">
          {moments.length > 0
            ? `Flagged sections (${moments.length})`
            : "No flagged sections"}
        </h2>

        <div className="mt-3 space-y-4">
          {moments.map((m, i) => (
            <div key={i} className="rounded-xl border p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm text-muted-foreground">
                  {fmtTime(m.start)}
                  {m.end > m.start ? `–${fmtTime(m.end)}` : ""}
                </span>
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
              {m.text && (
                <p className="mt-1 text-sm text-muted-foreground">{m.text}</p>
              )}
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
