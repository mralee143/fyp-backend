"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { PlusIcon, SearchIcon, Trash2Icon, VideoIcon } from "lucide-react";
import { toast } from "sonner";

import { useChatStore } from "@/store/chat";

/**
 * The "Recent chats" section of the app sidebar.
 *
 * Rendered by AppShell only on `/chat`. It reads from the shared chat store, so
 * the list, the open conversation and the page body never disagree.
 */

export function ChatSessionsPanel() {
  const router = useRouter();

  const sessions = useChatStore((s) => s.sessions);
  const activeId = useChatStore((s) => s.activeId);
  const health = useChatStore((s) => s.health);
  const init = useChatStore((s) => s.init);
  const open = useChatStore((s) => s.open);
  const newChat = useChatStore((s) => s.newChat);
  const remove = useChatStore((s) => s.remove);

  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    init().catch(() => toast.error("Could not load your conversations."));
  }, [init]);

  useEffect(() => {
    if (searching) searchRef.current?.focus();
  }, [searching]);

  const needle = query.trim().toLowerCase();
  const visible = needle
    ? sessions.filter((s) => s.title.toLowerCase().includes(needle))
    : sessions;

  return (
    <div className="mt-4 flex min-h-0 flex-1 flex-col px-2">
      <div className="flex flex-col gap-0.5">
        <button
          type="button"
          onClick={() => {
            newChat()
              .then(() => router.push("/chat"))
              .catch(() => toast.error("Could not start a new chat."));
          }}
          className="group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
        >
          <PlusIcon className="size-4 shrink-0 transition-transform duration-300 group-hover:scale-110" />
          <span className="truncate">New chat</span>
        </button>

        <button
          type="button"
          onClick={() => setSearching((s) => !s)}
          aria-expanded={searching}
          className="group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
        >
          <SearchIcon className="size-4 shrink-0 transition-transform duration-300 group-hover:scale-110" />
          <span className="truncate">Search chats</span>
        </button>
      </div>

      {searching && (
        <input
          ref={searchRef}
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter chats..."
          className="mt-2 w-full rounded-lg bg-muted px-2.5 py-1.5 text-sm outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
        />
      )}

      <p className="mt-4 px-2.5 pb-2 text-xs font-medium text-muted-foreground">
        Recent chats
      </p>

      <div className="-mx-1 min-h-0 flex-1 overflow-y-auto px-1">
        {visible.length === 0 ? (
          <p className="px-2.5 text-xs text-muted-foreground">
            {sessions.length === 0
              ? "No chats yet — start one above."
              : "No chats match that filter."}
          </p>
        ) : (
          <ul className="flex flex-col gap-0.5">
            {visible.map((session) => {
              const current = session.id === activeId;
              return (
                <li key={session.id} className="group/item relative">
                  <button
                    type="button"
                    onClick={() => {
                      open(session.id).catch(() =>
                        toast.error("Could not open that conversation.")
                      );
                      router.push("/chat");
                    }}
                    className={[
                      "flex w-full items-center gap-2 rounded-lg py-2 pe-8 ps-2.5 text-start text-sm transition-colors",
                      current
                        ? "bg-muted font-medium text-foreground"
                        : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                    ].join(" ")}
                  >
                    {session.video_name && <VideoIcon className="size-3.5 shrink-0" />}
                    <span className="truncate">{session.title}</span>
                  </button>

                  <button
                    type="button"
                    aria-label={`Delete ${session.title}`}
                    title="Delete chat"
                    onClick={() => {
                      remove(session.id).catch(() =>
                        toast.error("Could not delete that chat.")
                      );
                    }}
                    className="absolute end-1 top-1/2 grid size-7 -translate-y-1/2 place-items-center rounded-lg text-muted-foreground opacity-0 transition-opacity hover:bg-destructive/10 hover:text-destructive focus-visible:opacity-100 group-hover/item:opacity-100"
                  >
                    <Trash2Icon className="size-3.5" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {health && (
        <div className="mt-2 flex items-center gap-2 px-2.5 py-2 text-xs text-muted-foreground">
          <span
            className={[
              "size-2 shrink-0 rounded-full",
              health.online
                ? "bg-green-500"
                : health.local_fallback
                  ? "bg-amber-500"
                  : "bg-destructive",
            ].join(" ")}
          />
          <span className="truncate">
            {health.online
              ? health.model
              : health.local_fallback
                ? "Qwen offline — local fallback"
                : "Qwen offline"}
          </span>
        </div>
      )}
    </div>
  );
}
