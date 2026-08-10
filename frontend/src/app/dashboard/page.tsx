"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useAuthStore } from "@/store/auth";
import { logout } from "@/lib/auth";
import { VideoUpload } from "@/components/video-upload";
import {
  getStats,
  getHistory,
  type ScanStats,
  type ScanHistoryItem,
} from "@/lib/detection";

export default function DashboardPage() {
  const router = useRouter();
  const email = useAuthStore((s) => s.email);
  const [ready, setReady] = useState(false);
  const [stats, setStats] = useState<ScanStats | null>(null);
  const [history, setHistory] = useState<ScanHistoryItem[]>([]);

  // Client-side route protection: bounce to /login if no token.
  useEffect(() => {
    if (!useAuthStore.getState().token) {
      router.replace("/login");
    } else {
      setReady(true);
    }
  }, [router]);

  // Load real scan stats + history once authenticated.
  useEffect(() => {
    if (!ready) return;
    getStats().then(setStats).catch(() => {});
    getHistory().then(setHistory).catch(() => {});
  }, [ready]);

  if (!ready) return null;

  function handleLogout() {
    logout();
    router.replace("/login");
  }

  const cards = [
    { label: "Total scans", value: stats?.total ?? 0 },
    { label: "Threats found", value: stats?.threats ?? 0 },
    { label: "Clear", value: stats?.clear ?? 0 },
  ];

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-2 font-semibold">
            <span className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground">
              S
            </span>
            SentinelAI
          </div>
          <div className="flex items-center gap-3">
            <span className="hidden text-sm text-muted-foreground sm:inline">
              {email ?? "Signed in"}
            </span>
            <Button variant="outline" size="sm" onClick={handleLogout}>
              Log out
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-10">
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

        <div className="mt-8">
          <VideoUpload />
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
                No scans yet — upload a video above to get started.
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
      </main>
    </div>
  );
}
