import {
  ChartNoAxesColumnIcon,
  LayoutDashboardIcon,
  MessageSquareIcon,
  ShieldIcon,
  type LucideIcon,
} from "lucide-react";

/**
 * The application's primary navigation.
 *
 * Single source of truth for the sidebar: adding a route here is all it takes
 * for it to appear, get its active highlight, and show in the mobile drawer.
 */
export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Only rendered for admin users. */
  adminOnly?: boolean;
}

export const NAV_ITEMS: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboardIcon },
  // Analysis lives in the agent chat: attach a video there and it is scanned
  // on upload, so there is no separate "analyse" destination.
  { href: "/chat", label: "Agent chat", icon: MessageSquareIcon },
  { href: "/results", label: "Scan results", icon: ChartNoAxesColumnIcon },
  { href: "/admin", label: "Admin", icon: ShieldIcon, adminOnly: true },
];

/**
 * Routes with no nav entry of their own, mapped to the item that should stay
 * highlighted while you're on them (a report is opened from the dashboard).
 */
const ALIASES: Record<string, string> = {
  "/report": "/dashboard",
};

/** True when `href` is the nav item that should be highlighted for `pathname`. */
export function isActivePath(pathname: string, href: string): boolean {
  for (const [prefix, target] of Object.entries(ALIASES)) {
    if (pathname === prefix || pathname.startsWith(`${prefix}/`)) {
      return href === target;
    }
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}
