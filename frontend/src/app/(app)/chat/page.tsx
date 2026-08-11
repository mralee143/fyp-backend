"use client";

import { useEffect, useRef, useState } from "react";
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
import { useChatStore } from "@/store/chat";

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

/** Detectors the upload can be analysed with — same set as the analyser page. */
const MODELS = [
  { value: "auto", label: "Auto", hint: "Every model in turn — best coverage" },
  { value: "yolo", label: "Weapons", hint: "Fast, local: guns, knives, grenades" },
  { value: "action", label: "Actions", hint: "Fighting, robbery, accidents" },
  { value: "llm", label: "Gemini", hint: "Understands context and harassment" },
  { value: "qwen", label: "Qwen", hint: "Offline, slow but thorough" },
];

/** Human-readable file size for the attachment chip. */
function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function ChatPage() {
  const detail = useChatStore((s) => s.detail);
  const activeId = useChatStore((s) => s.activeId);
  const sending = useChatStore((s) => s.sending);
  const uploadPercent = useChatStore((s) => s.uploadPercent);
  const panelOpen = useChatStore((s) => s.panelOpen);
  const submit = useChatStore((s) => s.submit);
  const togglePanel = useChatStore((s) => s.togglePanel);

  const [input, setInput] = useState("");
  // Staged by the paperclip, uploaded and analysed by the arrow.
  const [pending, setPending] = useState<File | null>(null);
  const [model, setModel] = useState("auto");
  const fileRef = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const messages = detail?.messages ?? [];
  const hasVideo = Boolean(detail?.video_url);

  // Never leave the SSE connection open behind us — the browser would keep
  // reconnecting to a finished job.
  useEffect(() => () => useChatStore.getState().stopStream(), []);

  // Keep the newest message in view.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [detail?.messages, sending]);

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
      await submit(message, file, { model });
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
    <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
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

          {sending && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <span className="inline-block size-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
              Thinking — running detection can take a minute...
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      {/* Composer */}
      <div className="shrink-0 border-t bg-background">
        <div className="mx-auto w-full max-w-3xl px-4 py-3">
          {/* Which detector the next upload is analysed with. */}
          <div className="mb-2 flex items-center gap-1.5 overflow-x-auto">
            <span className="shrink-0 text-xs text-muted-foreground">Analyse with</span>
            {MODELS.map((m) => (
              <button
                key={m.value}
                type="button"
                onClick={() => setModel(m.value)}
                title={m.hint}
                aria-pressed={model === m.value}
                className={[
                  "shrink-0 rounded-full border px-2.5 py-1 text-xs transition-colors",
                  model === m.value
                    ? "border-primary bg-primary/10 font-medium text-primary"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                ].join(" ")}
              >
                {m.label}
              </button>
            ))}
          </div>

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
            The agent runs YOLO and the other detectors on demand, and queries your scan
            history. Verify anything critical against the report.
          </p>
          </div>
        </div>
      </div>

      {/* Video review panel — the agent drives this player. */}
      {panelOpen && (
        <aside
          aria-label="Video review"
          className="order-first flex max-h-[45vh] w-full shrink-0 overflow-hidden border-b bg-card lg:order-last lg:max-h-none lg:w-96 lg:border-b-0 lg:border-s"
        >
          {/* Keyed on the source so a new upload gets a fresh, reset player. */}
          <VideoPanel key={detail?.video_url ?? "empty"} />
        </aside>
      )}
    </div>
  );
}
