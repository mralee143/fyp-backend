"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";
import {
  FileVideoIcon,
  PaperclipIcon,
  PanelRightCloseIcon,
  PanelRightIcon,
  SendIcon,
  ShieldIcon,
  XIcon,
} from "lucide-react";
import { toast } from "sonner";

import { LiveAnalysisCard } from "@/components/chat/live-analysis-card";
import { ToolCard } from "@/components/chat/tool-card";
import { VideoPanel } from "@/components/chat/video-panel";
import { validateVideo } from "@/lib/detection";
import {
  DEFAULT_PANEL_WIDTH,
  WIDE_PANEL_WIDTH,
  useChatStore,
  useIsBusy,
  useTurnState,
  useUploadPercent,
  type TurnPhase,
} from "@/store/chat";

/**
 * Agent chat: attach a video and a question, then send them together.
 *
 * The paperclip only stages the file. Everything happens on the arrow: the
 * upload, the frame-by-frame analysis (the same pipeline the standalone
 * analyser runs, including the still captured at each flagged moment), and
 * finally the question itself — so the answer is given with the detection
 * already in hand.
 *
 * The page only renders — conversation state lives in the chat store, shared
 * with the sidebar list. Tool calls come back with each reply and render as
 * cards so an operator can see which detector produced which timestamp.
 */

const SUGGESTIONS = [
  "Show me the exact frame where the accident happens",
  "What weapons appear in this video?",
  "Give me a full threat analysis of the footage",
  "How many threats have I found across all my scans?",
];


/**
 * What the chat is actually waiting on, stage by stage.
 *
 * "Running detection" used to sit there for the whole turn, so a completed
 * analysis looked stuck — the detectors had finished minutes earlier and it was
 * the chat model still generating. Each stage now says which one is which.
 */
const PHASE_TEXT: Record<TurnPhase, { title: string; hint: string }> = {
  uploading: {
    title: "Uploading the video…",
    hint: "The analysis starts as soon as the bytes land.",
  },
  analysing: {
    title: "Running detection…",
    hint: "Frames and incidents appear in the card above as the worker finds them.",
  },
  summarising: {
    title: "Writing up the findings…",
    hint: "Reading back what the detectors stored — no model involved.",
  },
  thinking: {
    title: "Thinking…",
    hint: "Detection is done; the chat model is composing the reply.",
  },
  writing: {
    title: "Writing the reply…",
    hint: "The answer is appearing above as the model generates it.",
  },
};

/** Seconds a turn has been running -> "1m 20s". */
function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${(seconds % 60).toString().padStart(2, "0")}s`;
}

/** Human-readable file size for the attachment chip. */
function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function ChatPage() {
  const detail = useChatStore((s) => s.detail);
  const activeId = useChatStore((s) => s.activeId);
  // All three are per conversation: another chat thinking must not disable or
  // relabel this one.
  const sending = useIsBusy();
  const turn = useTurnState();
  const uploadPercent = useUploadPercent();
  const panelOpen = useChatStore((s) => s.panelOpen);
  const panelWidth = useChatStore((s) => s.panelWidth);
  const setPanelWidth = useChatStore((s) => s.setPanelWidth);
  const submit = useChatStore((s) => s.submit);
  const togglePanel = useChatStore((s) => s.togglePanel);

  const [input, setInput] = useState("");
  // Staged by the paperclip, uploaded and analysed by the arrow.
  const [pending, setPending] = useState<File | null>(null);
  /** Ticked once a second while a turn runs, to drive the elapsed readout. */
  const [now, setNow] = useState(() => Date.now());
  const fileRef = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const asideRef = useRef<HTMLElement>(null);
  /** True while the divider is being dragged — kills the width transition. */
  const [dragging, setDragging] = useState(false);

  /**
   * The divider drag. Pointer capture keeps the moves coming even when the
   * cursor outruns the 8px handle, which it does on any real drag.
   */
  function startDrag(e: React.PointerEvent<HTMLDivElement>) {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    setDragging(true);
  }

  function onDrag(e: React.PointerEvent<HTMLDivElement>) {
    if (!dragging) return;
    const right = asideRef.current?.getBoundingClientRect().right;
    if (right === undefined) return;
    // The panel is anchored to the right edge, so its width is whatever is
    // left between the pointer and that edge. The store clamps it.
    setPanelWidth(right - e.clientX);
  }

  function endDrag(e: React.PointerEvent<HTMLDivElement>) {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
    setDragging(false);
  }

  /** Which end of the range the panel is at — drives the arrow and the height. */
  const panelWide = panelWidth > (DEFAULT_PANEL_WIDTH + WIDE_PANEL_WIDTH) / 2;

  const messages = detail?.messages ?? [];
  const hasVideo = Boolean(detail?.video_url);

  // Never leave the SSE connection open behind us — the browser would keep
  // reconnecting to a finished job.
  useEffect(() => () => useChatStore.getState().stopStream(), []);

  // Keep the newest message in view.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [detail?.messages, sending]);

  // Count the wait out loud. A local model answers in minutes, and a spinner
  // with no clock on it is indistinguishable from one that has hung. The start
  // comes from the store, so leaving the chat and coming back keeps the total
  // honest instead of restarting it at zero.
  const startedAt = turn?.startedAt ?? null;
  useEffect(() => {
    if (startedAt === null) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [startedAt]);

  // Clamped, because `now` is only refreshed by the interval: the first render
  // of a new turn would otherwise read off a timestamp taken before it began.
  const elapsed =
    startedAt === null ? 0 : Math.max(0, Math.floor((now - startedAt) / 1000));

  /** The paperclip stages the file; nothing leaves the browser until send. */
  function handlePick(file: File) {
    const problem = validateVideo(file);
    if (problem) {
      toast.error(problem);
      return;
    }
    setPending(file);
  }

  async function handleSend(text?: string) {
    const message = (text ?? input).trim();
    if ((!message && !pending) || !activeId || sending) return;

    const file = pending;
    setInput("");
    setPending(null);
    try {
      // One turn: upload, analyse (frames + the exact moment), then answer.
      // The cascade picks the detector itself — Gemini first, local models
      // behind it. There is no manual choice to get wrong.
      await submit(message, file, { model: "auto" });
    } catch {
      toast.error(
        file
          ? "Upload or analysis failed. Check the backend is running."
          : "The agent could not answer. Check the backend is running."
      );
    }
  }

  return (
    // Narrow screens stack the review panel above the conversation; wide ones
    // put it alongside. Either way there is exactly one <video> on the page, so
    // the agent's playback commands always land on the player you can see.
    <div
      className={`flex min-h-0 flex-1 flex-col lg:flex-row ${
        dragging ? "cursor-col-resize select-none" : ""
      }`}
    >
      {/* Conversation column */}
      <div className="order-last flex min-w-0 flex-1 flex-col lg:order-first">
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-3xl space-y-4 px-4 py-6">
            {/* Panel toggle — the video itself lives in the review panel. */}
            <div className="flex justify-end">
              <button
                type="button"
                onClick={togglePanel}
                aria-expanded={panelOpen}
                title={panelOpen ? "Hide video review" : "Show video review"}
                className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                {panelOpen ? (
                  <PanelRightCloseIcon className="size-3.5" />
                ) : (
                  <PanelRightIcon className="size-3.5" />
                )}
                {panelOpen ? "Hide video" : "Show video"}
              </button>
            </div>

            {messages.length === 0 && (
            <div className="py-10 text-center">
              <h2 className="text-xl font-semibold tracking-tight">
                What&apos;s in your footage?
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                {hasVideo
                  ? "Ask me anything about the attached video."
                  : "Attach a video and hit send — I'll analyse it and show you the exact frames."}
              </p>
              <div className="mt-5 flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => handleSend(s)}
                    disabled={sending}
                    className="rounded-full border px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((message) => {
            if (message.role === "user") {
              return (
                <div key={message.id} className="flex justify-end">
                  <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl bg-primary px-3.5 py-2 text-sm text-primary-foreground">
                    {message.content}
                  </div>
                </div>
              );
            }

            if (message.role === "tool") {
              return (
                <ToolCard
                  key={message.id}
                  name={message.tool_name ?? message.content}
                  args={message.tool_payload?.arguments}
                  result={message.tool_payload?.result ?? {}}
                />
              );
            }

            return (
              <div key={message.id} className="flex gap-3">
                <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full bg-primary/10 text-primary">
                  <ShieldIcon className="size-4" />
                </span>
                <div className="whitespace-pre-wrap text-sm">{message.content}</div>
              </div>
            );
          })}

          <LiveAnalysisCard />

          {turn && (
            <div className="flex items-start gap-2 text-sm text-muted-foreground">
              <span className="mt-0.5 inline-block size-4 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent" />
              <span className="min-w-0">
                <span className="block">
                  {PHASE_TEXT[turn.phase].title}
                  <span className="ms-2 font-mono text-xs">
                    {formatElapsed(elapsed)}
                  </span>
                </span>
                <span className="block text-xs">{PHASE_TEXT[turn.phase].hint}</span>
                {/* Long silences are normal here, but only if you know that.
                    Say so rather than let it read as a hang — without naming
                    which model is answering, which is not the reader's
                    problem. */}
                {turn.phase === "thinking" && elapsed >= 45 && (
                  <span className="block text-xs">
                    Still generating. A reply can take several minutes — you can
                    open another chat meanwhile; this one will finish on its own.
                  </span>
                )}
              </span>
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      {/* Composer */}
      <div className="shrink-0 border-t bg-background">
        <div className="mx-auto w-full max-w-3xl px-4 py-3">
          {/* The attachment, shown the way any chat shows one. A staged file
              takes precedence: it is what the next send will analyse. */}
          {pending ? (
            <div className="mb-2 flex items-center gap-2 rounded-xl border bg-muted/30 px-2.5 py-2">
              <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
                <FileVideoIcon className="size-4" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">
                  {pending.name}
                </span>
                <span className="block text-xs text-muted-foreground">
                  {uploadPercent !== null
                    ? `Uploading… ${uploadPercent}%`
                    : sending
                      ? "Analysing…"
                      : `${formatBytes(pending.size)} · send to analyse — I'll pull out the exact frames`}
                </span>
              </span>
              <button
                type="button"
                onClick={() => setPending(null)}
                disabled={sending}
                aria-label="Remove attachment"
                title="Remove attachment"
                className="grid size-7 shrink-0 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
              >
                <XIcon className="size-4" />
              </button>
            </div>
          ) : hasVideo ? (
            <div className="mb-2 flex items-center gap-2 rounded-xl border bg-muted/30 px-2.5 py-2">
              <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
                <FileVideoIcon className="size-4" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">
                  {detail?.video_name}
                </span>
                <span className="block text-xs text-muted-foreground">
                  {sending ? "Working…" : "analysed — ask about it below"}
                </span>
              </span>
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                disabled={sending}
                className="shrink-0 rounded-lg px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
              >
                Replace
              </button>
            </div>
          ) : null}

          {uploadPercent !== null && (
            <div className="mb-2 h-1 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-primary transition-all"
                style={{ width: `${uploadPercent}%` }}
              />
            </div>
          )}

          <div className="flex items-end gap-2 rounded-2xl border p-1.5 focus-within:ring-2 focus-within:ring-ring">
            <input
              ref={fileRef}
              type="file"
              accept="video/*"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handlePick(file);
                e.target.value = "";
              }}
            />
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              disabled={sending || !activeId}
              aria-label="Attach video"
              title="Attach a video"
              className="grid size-9 shrink-0 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
            >
              <PaperclipIcon className="size-4" />
            </button>

            <textarea
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              placeholder={
                pending
                  ? "Ask about this video — or just send to analyse it..."
                  : hasVideo
                    ? "Ask about the video..."
                    : "Attach a video, or ask about past scans..."
              }
              className="max-h-40 grow resize-none bg-transparent px-1 py-2 text-sm outline-none placeholder:text-muted-foreground"
            />

            <button
              type="button"
              onClick={() => handleSend()}
              disabled={sending || (!input.trim() && !pending)}
              aria-label={pending ? "Send and analyse the video" : "Send message"}
              title={pending ? "Analyse this video" : "Send"}
              className="grid size-9 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground transition-opacity hover:opacity-90 disabled:pointer-events-none disabled:opacity-40"
            >
              <SendIcon className="size-4" />
            </button>
          </div>

          <p className="mt-1.5 text-center text-xs text-muted-foreground">
            The agent runs the detectors on demand and queries your scan
            history. Verify anything critical against the report.
          </p>
          </div>
        </div>
      </div>

      {/* Video review panel — the agent drives this player.
          Two ways to make it bigger: drag the divider on its edge, or the arrow
          in its header. Stacked layouts ignore the width and take more height
          instead, since there is only one column to give. */}
      {panelOpen && (
        <aside
          ref={asideRef}
          aria-label="Video review"
          style={{ "--panel-w": `${panelWidth}px` } as CSSProperties}
          className={`relative order-first flex w-full shrink-0 overflow-hidden border-b bg-card lg:order-last lg:max-h-none lg:w-[var(--panel-w)] lg:border-b-0 lg:border-s ${
            panelWide ? "max-h-[75vh]" : "max-h-[45vh]"
          } ${dragging ? "" : "transition-[width,max-height] duration-200"}`}
        >
          {/* The divider itself, made draggable. Double-click puts it back. */}
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="Drag to resize the video panel"
            aria-valuenow={panelWidth}
            tabIndex={0}
            onPointerDown={startDrag}
            onPointerMove={onDrag}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
            onDoubleClick={() => setPanelWidth(DEFAULT_PANEL_WIDTH)}
            onKeyDown={(e) => {
              // The panel grows leftwards, so left widens it.
              if (e.key === "ArrowLeft") setPanelWidth(panelWidth + 32);
              else if (e.key === "ArrowRight") setPanelWidth(panelWidth - 32);
              else return;
              e.preventDefault();
            }}
            title="Drag to resize · double-click to reset"
            className="group absolute inset-y-0 start-0 z-20 hidden w-2 cursor-col-resize touch-none lg:block"
          >
            <span
              className={`absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 transition-colors ${
                dragging ? "bg-primary" : "bg-transparent group-hover:bg-primary/60"
              }`}
            />
          </div>

          {/* Keyed on the source so a new upload gets a fresh, reset player. */}
          <VideoPanel key={detail?.video_url ?? "empty"} />
        </aside>
      )}
    </div>
  );
}
