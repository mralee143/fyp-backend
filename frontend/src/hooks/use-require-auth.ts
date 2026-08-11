"use client";

import { useEffect, useSyncExternalStore } from "react";
import { useRouter } from "next/navigation";

import { useAuthStore } from "@/store/auth";

/** Stable no-op subscribe: the value below never changes after mount. */
const noopSubscribe = () => () => {};

/**
 * Client-side route guard for everything under `app/(app)`.
 *
 * The token is persisted to localStorage, so it can't be read while the page
 * is prerendered. `useSyncExternalStore` gives us a hydration-safe "we're on
 * the client now" flag — server and first client render both see `false`, so
 * the markup matches — instead of flipping a state flag inside an effect,
 * which would trigger a cascading re-render.
 *
 * @returns `true` once an authenticated session is confirmed.
 */
export function useRequireAuth(): boolean {
  const router = useRouter();

  const onClient = useSyncExternalStore(
    noopSubscribe,
    () => true, // client: localStorage has been rehydrated synchronously
    () => false // server + first client render
  );
  const token = useAuthStore((s) => s.token);

  useEffect(() => {
    if (onClient && !token) {
      router.replace("/login");
    }
  }, [onClient, token, router]);

  return onClient && Boolean(token);
}
