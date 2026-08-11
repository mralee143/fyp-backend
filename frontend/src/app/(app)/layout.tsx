"use client";

import { AppShell } from "@/components/app-shell";
import { useRequireAuth } from "@/hooks/use-require-auth";

/**
 * Layout for every signed-in route.
 *
 * Guards the whole group in one place — pages below no longer check the token
 * themselves — and wraps them in the shared sidebar/top-bar shell, which stays
 * mounted across navigations.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  const ready = useRequireAuth();

  // Render nothing until the token check resolves, so a protected page never
  // flashes before the redirect to /login.
  if (!ready) return null;

  return <AppShell>{children}</AppShell>;
}
