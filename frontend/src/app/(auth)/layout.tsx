import Link from "next/link";
import { ShieldIcon } from "lucide-react";

/**
 * Layout for the sign-in flows (login, signup, verify, password reset).
 *
 * Gives all five pages one centred, branded frame so each page only has to
 * render its own card.
 */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 bg-muted/30 px-4 py-10">
      <Link href="/" className="flex items-center gap-2 font-semibold">
        <span className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground">
          <ShieldIcon className="size-4" />
        </span>
        SentinelAI
      </Link>

      <div className="w-full max-w-sm">{children}</div>

      <p className="text-center text-xs text-muted-foreground">
        AI-powered detection of violence, crime and weapons in video footage.
      </p>
    </div>
  );
}
