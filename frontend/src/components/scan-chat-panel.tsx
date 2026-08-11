"use client";

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { errorMessage } from "@/lib/auth";
import { mediaUrl } from "@/lib/detection";
import { formatTime } from "@/lib/jobs";
import {
  askQuestion,
  explainIncident,
  getConversation,
  type ChatAnswer,
  type ChatMessage,
  type IncidentAnalysis,
} from "@/lib/scan-chat";

/** One incident handed to the panel from the timeline. */
export interface IncidentFocus {
  ordinal: number;
  label: string;
  start: number;
  end: number;
  clipUrl?: string;
  /** Changes on every click, so re-picking the same incident asks again. */
  token: number;
}

/**
 * Ask questions about one analysed video.
 *
 * The agent is grounded in that scan's stored analysis and its extracted
 * frames, so it answers about what the pipeline actually saw. When a question
 * falls outside the analysis it says so rather than inventing a scene — the
 * panel marks those answers instead of hiding the distinction.
 *
 * Picking a section off the timeline narrows it further: the panel then asks
 * about that clip alone, against stills taken across the incident, and the
 * reply arrives with the exact frame the agent read it from. Everything stays
 * in one conversation, so a follow-up still knows what was just discussed.
 */
export function ScanChatPanel({
  scanId,
  focus = null,
  onClearFocus,
}: {
  scanId: number;
  focus?: IncidentFocus | null;
  onClearFocus?: () => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [lastAnswer, setLastAnswer] = useState<ChatAnswer | null>(null);
  // Clip and key frame per answer, so the evidence stays with the reply it
  // belongs to rather than only with the most recent one.
  const [evidence, setEvidence] = useState<Record<number, IncidentAnalysis>>({});
  const endRef = useRef<HTMLDivElement>(null);
  const handledToken = useRef<number | null>(null);
  const pendingId = useRef(0);

  useEffect(() => {
    getConversation(scanId)
      .then((conversation) => {
        setMessages(conversation.messages);
        setSuggestions(conversation.suggestions);
      })
      .catch(() => {
        /* the panel still works; the first question creates the session */
      })
      .finally(() => setLoaded(true));
  }, [scanId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, sending]);

  /** Add the assistant's reply, and any clip evidence that came with it. */
  function appendAnswer(answer: ChatAnswer, analysis?: IncidentAnalysis) {
    setLastAnswer(answer);
    setMessages((current) => [
      ...current,
      {
        id: answer.message_id,
        ordinal: (current[current.length - 1]?.ordinal ?? -1) + 1,
        role: "assistant",
        content: answer.answer,
        latency_ms: answer.latency_ms,
        created_at: new Date().toISOString(),
        citations: answer.citations.map((c) => ({
          segment_id: c.segment_id ?? null,
          image_id: c.image_id ?? null,
        })),
      },
    ]);
    if (analysis) {
      setEvidence((current) => ({ ...current, [answer.message_id]: analysis }));
    }
  }

  async function send(question: string) {
    const trimmed = question.trim();
    if (!trimmed || sending) return;

    setInput("");
    setSending(true);

    // Show the question immediately; the server assigns the real ordinals.
    // Negative ids keep it distinct from anything the server will send back.
    const optimistic: ChatMessage = {
      id: -(pendingId.current += 1),
      ordinal: messages.length,
      role: "user",
      content: trimmed,
      latency_ms: null,
      created_at: new Date().toISOString(),
      citations: [],
    };
    setMessages((current) => [...current, optimistic]);

    try {
      // While a section is focused the question is answered against that clip
      // and its stills, not the whole video.
      const answer = focus
        ? await explainIncident(scanId, focus.ordinal, trimmed)
        : await askQuestion(scanId, trimmed);
      appendAnswer(answer, focus ? (answer as IncidentAnalysis) : undefined);
    } catch (error) {
      setMessages((current) => current.filter((m) => m.id !== optimistic.id));
      setInput(trimmed);
      toast.error(errorMessage(error));
    } finally {
      setSending(false);
    }
  }

  // A section picked off the timeline asks the agent to read that clip. The
  // token changes per click, so the same incident can be asked about twice.
  useEffect(() => {
    if (!focus || handledToken.current === focus.token) return;
    handledToken.current = focus.token;

    let cancelled = false;
    setSending(true);
    explainIncident(scanId, focus.ordinal)
      .then((analysis) => {
        if (cancelled) return;
        setMessages((current) => [
          ...current,
          {
            id: -(pendingId.current += 1),
            ordinal: (current[current.length - 1]?.ordinal ?? -1) + 1,
            role: "user",
            content: analysis.question,
            latency_ms: null,
            created_at: new Date().toISOString(),
            citations: [],
          },
        ]);
        appendAnswer(analysis, analysis);
      })
      .catch((error) => {
        if (!cancelled) toast.error(errorMessage(error));
      })
      .finally(() => {
        if (!cancelled) setSending(false);
      });

    return () => {
      cancelled = true;
    };
    // appendAnswer only touches setState, so the effect follows the click alone.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus?.token, focus?.ordinal, scanId]);

  return (
    <section className="rounded-xl border">
      <header className="border-b px-4 py-3">
        <h2 className="font-semibold">Ask about this video</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Answers come from this analysis and its extracted frames.
        </p>
        {focus && (
          <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/10 px-2.5 py-1.5">
            <span className="text-xs">
              Focused on <span className="font-medium">{focus.label}</span>{" "}
              <span className="font-mono">
                {formatTime(focus.start)}–{formatTime(focus.end)}
              </span>
            </span>
            <button
              type="button"
              onClick={onClearFocus}
              className="ml-auto text-xs text-muted-foreground underline-offset-2 hover:underline"
            >
              Ask about the whole video instead
            </button>
          </div>
        )}
      </header>

      <div className="max-h-[26rem] space-y-3 overflow-y-auto px-4 py-4">
        {!loaded && (
          <p className="text-sm text-muted-foreground">Loading conversation…</p>
        )}

        {loaded && messages.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No questions yet. Try one below, or ask your own.
          </p>
        )}

        {messages.map((message) => (
          <div key={message.id}>
            <div
              className={
                message.role === "user" ? "flex justify-end" : "flex justify-start"
              }
            >
              <div
                className={`max-w-[85%] rounded-2xl px-3.5 py-2 text-sm whitespace-pre-wrap ${
                  message.role === "user"
                    ? "bg-primary text-primary-foreground"
                    : "border bg-muted/40"
                }`}
              >
                {message.content}
              </div>
            </div>

            {/* The clip that was read, and the one frame it was read from. */}
            {evidence[message.id] && (
              <IncidentEvidence analysis={evidence[message.id]} />
            )}
          </div>
        ))}

        {sending && (
          <div className="flex justify-start">
            <div className="rounded-2xl border bg-muted/40 px-3.5 py-2 text-sm text-muted-foreground">
              {focus
                ? `Reading the clip at ${formatTime(focus.start)}–${formatTime(focus.end)}…`
                : "Thinking…"}
            </div>
          </div>
        )}

        {/* Show what the last answer leaned on, so a claim can be checked. */}
        {lastAnswer && lastAnswer.citations.length > 0 && (
          <div className="rounded-lg border border-dashed p-2.5">
            <p className="text-[11px] font-medium text-muted-foreground">
              Based on
            </p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {lastAnswer.citations.map((citation, index) => (
                <span
                  key={index}
                  className="rounded-full border px-2 py-0.5 text-[11px] text-muted-foreground"
                >
                  {citation.segment_id
                    ? `${citation.label ?? "Incident"} ${formatTime(citation.start_time)}`
                    : `Frame ${formatTime(citation.captured_at_seconds)}`}
                </span>
              ))}
            </div>
          </div>
        )}

        {lastAnswer && !lastAnswer.grounded && (
          <p className="text-[11px] text-amber-600 dark:text-amber-400">
            This question falls outside what the analysis covers.
          </p>
        )}

        {lastAnswer && lastAnswer.source === "analysis" && (
          <p className="text-[11px] text-muted-foreground">
            No language model was reachable — that reply is a direct readout of
            the stored analysis.
          </p>
        )}

        <div ref={endRef} />
      </div>

      {suggestions.length > 0 && messages.length === 0 && (
        <div className="flex flex-wrap gap-2 border-t px-4 py-3">
          {suggestions.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              onClick={() => send(suggestion)}
              disabled={sending}
              className="rounded-full border px-3 py-1 text-xs transition-colors hover:bg-muted disabled:opacity-50"
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}

      <form
        className="flex gap-2 border-t px-4 py-3"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={
            focus
              ? `Ask about the clip at ${formatTime(focus.start)}…`
              : "What happens at 0:14?"
          }
          disabled={sending}
          className="flex-1 rounded-lg border bg-background px-3 py-2 text-sm"
        />
        <Button type="submit" disabled={sending || !input.trim()}>
          Ask
        </Button>
      </form>
    </section>
  );
}

/**
 * What an incident answer was drawn from: the clip itself and the single frame
 * the agent named. Showing the frame is the point — "0:52.30, frame #1569" is
 * checkable in a way that "around the middle" is not.
 */
function IncidentEvidence({ analysis }: { analysis: IncidentAnalysis }) {
  const clip = mediaUrl(analysis.segment.annotated_clip_url ?? analysis.segment.clip_url);
  const frame = mediaUrl(analysis.key_frame?.url);

  if (!clip && !frame) return null;

  return (
    <div className="mt-2 grid gap-3 rounded-xl border border-dashed p-3 sm:grid-cols-2">
      {clip && (
        <figure>
          <video
            src={clip}
            controls
            preload="metadata"
            className="w-full rounded-lg border bg-black"
          />
          <figcaption className="mt-1 font-mono text-[11px] text-muted-foreground">
            Incident clip · {formatTime(analysis.segment.start_time)}–
            {formatTime(analysis.segment.end_time)}
          </figcaption>
        </figure>
      )}
      {frame && analysis.key_frame && (
        <figure>
          {/* eslint-disable-next-line @next/next/no-img-element --
              presigned MinIO URLs expire, so they cannot be optimised at
              build time. */}
          <img
            src={frame}
            alt={`Frame at ${analysis.key_frame.label}`}
            loading="lazy"
            className="w-full rounded-lg border bg-black object-contain"
          />
          <figcaption className="mt-1 font-mono text-[11px] text-muted-foreground">
            Key frame · {analysis.key_frame.label}
          </figcaption>
        </figure>
      )}
    </div>
  );
}
