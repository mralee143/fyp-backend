"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  MenuIcon,
  PanelLeftCloseIcon,
  PanelLeftIcon,
  ShieldIcon,
  XIcon,
} from "lucide-react";

import { ChatSessionsPanel } from "@/components/chat/chat-sessions-panel";
import { Button } from "@/components/ui/button";
import { displayName } from "@/lib/auth";
import { getMe } from "@/lib/admin";
import { NAV_ITEMS, isActivePath } from "@/lib/nav";
import { useAuthStore } from "@/store/auth";

/**
 * The shell every authenticated page renders inside: a collapsible sidebar for
 * primary navigation and a top bar for page context and the session menu.
 *
 * It lives in `app/(app)/layout.tsx`, so it mounts once and survives
 * navigation — the sidebar keeps its collapsed state and the chat list keeps
 * its data as you move between routes.
 *
 * The conversation list sits under the nav on every route, not just `/chat`, so
 * recent chats are always one click away; the footer identifies the signed-in
 * account and opens the profile, which is where signing out lives.
 */

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const storedEmail = useAuthStore((s) => s.email);

  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [isAdmin, setIsAdmin] = useState(false);
  // The server is the authority on the address; the stored one covers the gap
  // before /auth/me answers so the footer never renders blank.
  const [email, setEmail] = useState<string | null>(storedEmail);

  useEffect(() => {
    getMe()
      .then((me) => {
        setIsAdmin(me.is_admin);
        setEmail(me.email);
      })
      .catch(() => setIsAdmin(false));
  }, []);

  const items = NAV_ITEMS.filter((item) => !item.adminOnly || isAdmin);
  const active = items.find((item) => isActivePath(pathname, item.href));
  const onProfile = pathname.startsWith("/profile");
  const name = displayName(email);

  return (
    <div className="min-h-screen bg-background">
      {/* Sidebar */}
      <aside
        aria-label="Main navigation"
        className={[
          "fixed inset-y-0 start-0 z-50 flex flex-col overflow-hidden",
          "border-e bg-card transition-all duration-300",
          collapsed ? "w-16" : "w-64",
          mobileOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0",
        ].join(" ")}
      >
        {/* Brand + collapse toggle */}
        <header className="flex h-14 shrink-0 items-center justify-between gap-2 px-3">
          <Link
            href="/dashboard"
            className="flex items-center gap-2 overflow-hidden font-semibold"
          >
            <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-primary text-primary-foreground">
              <ShieldIcon className="size-4" />
            </span>
            {!collapsed && <span className="truncate">SentinelAI</span>}
          </Link>

          <button
            type="button"
            onClick={() => setCollapsed((c) => !c)}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="hidden size-8 shrink-0 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground md:grid"
          >
            {collapsed ? (
              <PanelLeftIcon className="size-4" />
            ) : (
              <PanelLeftCloseIcon className="size-4" />
            )}
          </button>

          <button
            type="button"
            onClick={() => setMobileOpen(false)}
            aria-label="Close navigation"
            className="grid size-8 shrink-0 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground md:hidden"
          >
            <XIcon className="size-4" />
          </button>
        </header>

        {/* Primary nav */}
        <nav className="px-2">
          <ul className="flex flex-col gap-0.5">
            {items.map((item) => {
              const Icon = item.icon;
              const current = isActivePath(pathname, item.href);
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    title={collapsed ? item.label : undefined}
                    aria-current={current ? "page" : undefined}
                    // Navigating always dismisses the mobile drawer.
                    onClick={() => setMobileOpen(false)}
                    className={[
                      "group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors",
                      current
                        ? "bg-muted font-medium text-foreground"
                        : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                    ].join(" ")}
                  >
                    <Icon className="size-4 shrink-0 transition-transform duration-300 group-hover:scale-110" />
                    {!collapsed && <span className="truncate">{item.label}</span>}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        {/* Every conversation, on every route. */}
        {!collapsed && <ChatSessionsPanel onNavigate={() => setMobileOpen(false)} />}

        {/* Who is signed in — and the way to the profile, where logging out is. */}
        <footer className="mt-auto shrink-0 border-t p-2">
          <Link
            href="/profile"
            onClick={() => setMobileOpen(false)}
            aria-current={onProfile ? "page" : undefined}
            title={collapsed ? `${name} — open profile` : "Open your profile"}
            className={[
              "flex items-center gap-2 rounded-lg transition-colors",
              collapsed ? "justify-center p-1.5" : "px-1.5 py-1.5",
              onProfile
                ? "bg-muted text-foreground"
                : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
            ].join(" ")}
          >
            <span className="grid size-7 shrink-0 place-items-center rounded-full bg-primary/10 text-xs font-semibold uppercase text-primary">
              {email?.[0] ?? "?"}
            </span>
            {!collapsed && (
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-foreground">
                  {name}
                </span>
                <span className="block truncate text-xs text-muted-foreground">
                  {email ?? "Signed in"}
                </span>
              </span>
            )}
          </Link>
        </footer>
      </aside>

      {/* Mobile scrim */}
      {mobileOpen && (
        <div
          role="presentation"
          onClick={() => setMobileOpen(false)}
          className="fixed inset-0 z-40 bg-foreground/40 md:hidden"
        />
      )}

      {/* Content */}
      <div
        className={`flex min-h-screen flex-col transition-all duration-300 ${
          collapsed ? "md:ps-16" : "md:ps-64"
        }`}
      >
        <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-3 border-b bg-background/80 px-4 backdrop-blur">
          <button
            type="button"
            onClick={() => setMobileOpen(true)}
            aria-label="Open navigation"
            className="grid size-9 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground md:hidden"
          >
            <MenuIcon className="size-4" />
          </button>

          <h1 className="truncate text-sm font-medium">
            {active?.label ?? (onProfile ? "Profile" : "SentinelAI")}
          </h1>

          <div className="ms-auto flex items-center gap-2">
            <Button
              render={<Link href="/chat" />}
              nativeButton={false}
              variant="outline"
              size="sm"
            >
              Ask the agent
            </Button>
          </div>
        </header>

        <main className="flex min-h-0 flex-1 flex-col">{children}</main>
      </div>
    </div>
  );
}
