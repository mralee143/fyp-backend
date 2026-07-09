"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
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

const SUMMARY = [
  { label: "Total scans", value: 0 },
  { label: "Threats found", value: 0 },
  { label: "Weapons", value: 0 },
  { label: "Actions", value: 0 },
];

export default function DashboardPage() {
  const router = useRouter();
  const email = useAuthStore((s) => s.email);
  const [ready, setReady] = useState(false);

  // Client-side route protection: bounce to /login if no token.
  useEffect(() => {
    if (!useAuthStore.getState().token) {
      router.replace("/login");
    } else {
      setReady(true);
    }
  }, [router]);

  if (!ready) return null;

  function handleLogout() {
    logout();
    router.replace("/login");
  }

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

        <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {SUMMARY.map((s) => (
            <Card key={s.label}>
              <CardHeader className="pb-2">
                <CardDescription>{s.label}</CardDescription>
                <CardTitle className="text-3xl">{s.value}</CardTitle>
              </CardHeader>
            </Card>
          ))}
        </div>

        <Card className="mt-8">
          <CardHeader>
            <CardTitle>Analyze a video</CardTitle>
            <CardDescription>
              Upload footage to scan for weapons, fights and other threats.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid place-items-center rounded-xl border border-dashed py-16 text-center text-muted-foreground">
              <p>Upload &amp; detection UI coming next (Phase 8).</p>
              <Button className="mt-4" disabled>
                Upload video
              </Button>
            </div>
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
