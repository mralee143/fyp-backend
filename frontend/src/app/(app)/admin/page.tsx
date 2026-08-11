"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useAuthStore } from "@/store/auth";
import {
  getMe,
  getAdminStats,
  getAdminUsers,
  getAdminScans,
  getAdminAnalytics,
  deleteUser,
  deleteScan,
  type AdminStats,
  type AdminUser,
  type AdminScan,
  type AdminAnalytics,
} from "@/lib/admin";
import { AdminCharts } from "@/components/admin-charts";
import { errorMessage } from "@/lib/auth";

export default function AdminPage() {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [adminEmail, setAdminEmail] = useState("");
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [scans, setScans] = useState<AdminScan[]>([]);
  const [analytics, setAnalytics] = useState<AdminAnalytics | null>(null);

  const load = useCallback(() => {
    getAdminStats().then(setStats).catch(() => {});
    getAdminUsers().then(setUsers).catch(() => {});
    getAdminScans().then(setScans).catch(() => {});
    getAdminAnalytics().then(setAnalytics).catch(() => {});
  }, []);

  useEffect(() => {
    if (!useAuthStore.getState().token) {
      router.replace("/login");
      return;
    }
    // Only the admin may view this page.
    getMe()
      .then((me) => {
        if (!me.is_admin) {
          router.replace("/dashboard");
          return;
        }
        setAdminEmail(me.email);
        setReady(true);
        load();
      })
      .catch(() => router.replace("/dashboard"));
  }, [router, load]);

  async function onDeleteUser(u: AdminUser) {
    if (!confirm(`Delete user ${u.email} and all their scans?`)) return;
    try {
      await deleteUser(u.id);
      toast.success("User deleted");
      load();
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  async function onDeleteScan(s: AdminScan) {
    if (!confirm(`Delete this scan (${s.filename})?`)) return;
    try {
      await deleteScan(s.id);
      toast.success("Scan deleted");
      load();
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  if (!ready) return null;

  const cards = [
    { label: "Total users", value: stats?.users ?? 0 },
    { label: "Total scans", value: stats?.scans ?? 0 },
    { label: "Threats detected", value: stats?.threats ?? 0 },
  ];

  return (
    <div className="mx-auto w-full max-w-6xl px-6 py-10">
        <h1 className="text-2xl font-bold tracking-tight">Admin dashboard</h1>
        <p className="mt-1 text-muted-foreground">
          Overview of all users and detections.
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

        {/* Charts */}
        {analytics && <AdminCharts data={analytics} />}

        {/* Users */}
        <h2 className="mt-10 text-lg font-semibold">Users ({users.length})</h2>
        <div className="mt-3 overflow-x-auto rounded-xl border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left">
              <tr>
                <th className="px-3 py-2">Email</th>
                <th className="px-3 py-2">Verified</th>
                <th className="px-3 py-2">Scans</th>
                <th className="px-3 py-2">Joined</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-t">
                  <td className="px-3 py-2">{u.email}</td>
                  <td className="px-3 py-2">
                    <span
                      className={`rounded-full border px-2 py-0.5 text-xs ${
                        u.is_active
                          ? "border-green-500/40 bg-green-500/10 text-green-600 dark:text-green-400"
                          : "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400"
                      }`}
                    >
                      {u.is_active ? "verified" : "pending"}
                    </span>
                  </td>
                  <td className="px-3 py-2">{u.scan_count}</td>
                  <td className="px-3 py-2 text-muted-foreground">
                    {new Date(u.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {u.email.toLowerCase() !== adminEmail.toLowerCase() && (
                      <button
                        onClick={() => onDeleteUser(u)}
                        className="text-sm font-medium text-destructive hover:underline"
                      >
                        Delete
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Scans */}
        <h2 className="mt-10 text-lg font-semibold">
          All scans ({scans.length})
        </h2>
        <div className="mt-3 overflow-x-auto rounded-xl border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left">
              <tr>
                <th className="px-3 py-2">User</th>
                <th className="px-3 py-2">Video</th>
                <th className="px-3 py-2">Result</th>
                <th className="px-3 py-2">When</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {scans.map((s) => (
                <tr
                  key={s.id}
                  onClick={() => router.push(`/report/${s.id}`)}
                  className="cursor-pointer border-t transition-colors hover:bg-muted/50"
                >
                  <td className="px-3 py-2">{s.email}</td>
                  <td className="max-w-[220px] truncate px-3 py-2">
                    {s.filename}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`rounded-full border px-2 py-0.5 text-xs ${
                        s.violence_detected
                          ? "border-destructive/40 bg-destructive/10 text-destructive"
                          : "border-green-500/40 bg-green-500/10 text-green-600 dark:text-green-400"
                      }`}
                    >
                      {s.violence_detected ? "Threat" : "Clear"}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">
                    {new Date(s.created_at).toLocaleString()}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onDeleteScan(s);
                      }}
                      className="text-sm font-medium text-destructive hover:underline"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
  );
}
