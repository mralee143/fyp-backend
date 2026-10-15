"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  CalendarIcon,
  KeyRoundIcon,
  LogOutIcon,
  MailIcon,
  ShieldCheckIcon,
  UserIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { getMe, type Me } from "@/lib/admin";
import { displayName, logout } from "@/lib/auth";
import { useAuthStore } from "@/store/auth";

/**
 * The signed-in user's account page, reached from the sidebar footer.
 *
 * Everything the backend knows about the account — there is no name column, so
 * the heading is derived from the email — plus the two things you come here to
 * do: change the password and sign out.
 */

/** ISO timestamp -> "12 August 2026", or a dash when the server sent nothing. */
function formatDate(iso?: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString(undefined, {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

/** One labelled row of the details card. */
function Detail({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof MailIcon;
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-3 px-4 py-3">
      <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <p className="text-xs text-muted-foreground">{label}</p>
        <div className="truncate text-sm font-medium">{value}</div>
      </div>
    </div>
  );
}

export default function ProfilePage() {
  const router = useRouter();
  const storedEmail = useAuthStore((s) => s.email);

  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getMe()
      .then(setMe)
      .catch(() => toast.error("Could not load your profile."))
      .finally(() => setLoading(false));
  }, []);

  // The stored address covers the moment before /auth/me answers, so the page
  // never renders an empty identity.
  const email = me?.email ?? storedEmail;
  const name = displayName(email);

  function handleLogout() {
    logout();
    router.replace("/login");
  }

  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-8">
      {/* Identity */}
      <div className="flex items-center gap-4">
        <span className="grid size-16 shrink-0 place-items-center rounded-full bg-primary/10 text-xl font-semibold uppercase text-primary">
          {email?.[0] ?? "?"}
        </span>
        <div className="min-w-0">
          <h2 className="truncate text-xl font-semibold tracking-tight">{name}</h2>
          <p className="truncate text-sm text-muted-foreground">
            {email ?? "Signed in"}
          </p>
          {me?.is_admin && (
            <span className="mt-1.5 inline-flex items-center gap-1 rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
              <ShieldCheckIcon className="size-3" />
              Administrator
            </span>
          )}
        </div>
      </div>

      {/* Account */}
      <section className="mt-8">
        <h3 className="px-1 text-sm font-medium">Account</h3>
        <div className="mt-2 divide-y rounded-xl border">
          <Detail icon={MailIcon} label="Email" value={email ?? "—"} />
          <Detail
            icon={UserIcon}
            label="Role"
            value={loading ? "…" : me?.is_admin ? "Administrator" : "Standard user"}
          />
          <Detail
            icon={ShieldCheckIcon}
            label="Status"
            value={
              loading ? (
                "…"
              ) : me?.is_active === false ? (
                <span className="text-destructive">Deactivated</span>
              ) : (
                <span className="text-green-600 dark:text-green-400">Active</span>
              )
            }
          />
          <Detail
            icon={CalendarIcon}
            label="Member since"
            value={loading ? "…" : formatDate(me?.created_at)}
          />
        </div>
      </section>

      {/* What you came here to do */}
      <section className="mt-8">
        <h3 className="px-1 text-sm font-medium">Security</h3>
        <div className="mt-2 divide-y rounded-xl border">
          <div className="flex flex-wrap items-center gap-3 px-4 py-3">
            <KeyRoundIcon className="size-4 shrink-0 text-muted-foreground" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">Password</p>
              <p className="text-xs text-muted-foreground">
                We email you a code to set a new one.
              </p>
            </div>
            <Button
              render={<Link href="/forgot-password" />}
              nativeButton={false}
              variant="outline"
              size="sm"
            >
              Change password
            </Button>
          </div>

          <div className="flex flex-wrap items-center gap-3 px-4 py-3">
            <LogOutIcon className="size-4 shrink-0 text-muted-foreground" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">Sign out</p>
              <p className="text-xs text-muted-foreground">
                Ends this session on this device. Analyses already running carry
                on server-side.
              </p>
            </div>
            <Button variant="destructive" size="sm" onClick={handleLogout}>
              <LogOutIcon />
              Log out
            </Button>
          </div>
        </div>
      </section>
    </div>
  );
}
