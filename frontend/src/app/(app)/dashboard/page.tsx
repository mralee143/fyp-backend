"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  getStats,
  getHistory,
  type ScanStats,
  type ScanHistoryItem,
} from "@/lib/detection";

export default function DashboardPage() {
  const [stats, setStats] = useState<ScanStats | null>(null);
  const [history, setHistory] = useState<ScanHistoryItem[]>([]);

  // The (app) layout guards the route, so this only runs when authenticated.
  useEffect(() => {
    getStats().then(setStats).catch(() => {});
    getHistory().then(setHistory).catch(() => {});
  }, []);

  const cards = [
    { label: "Total scans", value: stats?.total ?? 0 },
    { label: "Threats found", value: stats?.threats ?? 0 },
    { label: "Clear", value: stats?.clear ?? 0 },
  ];

  return (
    <div className="mx-auto w-full max-w-6xl px-6 py-10">
        <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>
        <p className="mt-1 text-muted-foreground">
          Overview of your video threat scans.
        </p>

        <div className="mt-8 grid gap-4 sm:grid-cols-3">
          {cards.map((s) => (
            <Card key={s.label}>
              <CardHeader className="pb-2">
                <CardDescription>{s.label}</CardDescription>
                <CardTitle className="text-3xl">{s.value}</CardTitle>
              </CardHeader>
            </Card>
          ))}
        </div>

        {/* Recent scans */}
        <Card className="mt-8">
          <CardHeader>
            <CardTitle>Recent scans</CardTitle>
            <CardDescription>Your last {history.length} detections</CardDescription>
          </CardHeader>
          <CardContent>
            {history.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                No scans yet —{" "}
                <Link href="/analyze" className="text-primary hover:underline">
                  analyse a video
                </Link>{" "}
                to get started.
              </p>
            ) : (
              <div className="divide-y">
                {history.map((h) => (
                  <Link
                    key={h.id}
                    href={`/report/${h.id}`}
                    className="flex items-center justify-between gap-4 py-3 -mx-2 px-2 rounded-lg transition-colors hover:bg-muted/50"
                  >
                    <div className="min-w-0">
                      <p className="truncate font-medium">{h.filename}</p>
                      <p className="truncate text-sm text-muted-foreground">
                        {h.summary || "—"}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-3">
                      <span className="rounded-full border px-2 py-0.5 text-xs text-muted-foreground">
                        {h.model}
                      </span>
                      <span
                        className={`rounded-full border px-2 py-0.5 text-xs ${
                          h.violence_detected
                            ? "border-destructive/40 bg-destructive/10 text-destructive"
                            : "border-green-500/40 bg-green-500/10 text-green-600 dark:text-green-400"
                        }`}
                      >
                        {h.violence_detected ? "Threat" : "Clear"}
                      </span>
                      <span className="hidden text-xs text-muted-foreground sm:inline">
                        {new Date(h.created_at).toLocaleString()}
                      </span>
                    </div>
                  </Link>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
    </div>
  );
}
