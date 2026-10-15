import { create } from "zustand";

import {
  analyzeVideo,
  attachVideo,
  createSession,
  deleteSession,
  getChatHealth,
  getSession,
  listSessions,
  streamMessage,
  type ChatHealth,
  type ChatMessage,
  type ChatSession,
  type ChatSessionDetail,
  type ToolResult,
} from "@/lib/chat";
import {
  getJob,
  subscribeToJob,
  type JobFrame,
  type JobSegment,
  type JobSubscription,
} from "@/lib/jobs";

/**
 * Agent chat state, shared by the conversation list in the sidebar and the
 * chat page itself. Keeping it here (rather than in the page) is what lets the
 * sidebar list stay in sync while living in a different part of the tree.
 *
 * Everything that describes work in progress — the in-flight turn, the upload
 * percentage, the streamed analysis and its event subscription — is keyed by
 * session id rather than held once for "the" conversation. A turn takes minutes
 * (detection plus inference), and a single global flag meant the whole UI was
 * frozen for its duration: you could not start a second chat, let alone ask it
 * anything, until the first one finished or timed out. Per session, each
 * conversation thinks on its own and the user can move between them freely.
 */

/** Optimistic placeholders use negative ids so they never clash with real rows. */
let tempId = -1;
const nextTempId = () => tempId--;

/** What the agent (or a click in the transcript) asked the player to do. */
export interface PlaybackCommand {
  action: "play" | "pause" | "replay" | "seek" | "play_segment";
  startTime?: number;
  endTime?: number;
  reason?: string;
  /** Bumped every time so repeating the same command re-fires the effect. */
  nonce: number;
}

let commandNonce = 0;

/** The video panel's narrow default — wide enough for the player and the bar. */
export const DEFAULT_PANEL_WIDTH = 384;
/** Where the arrow takes it: big enough to actually watch the footage. */
export const WIDE_PANEL_WIDTH = 720;
/** Below this the chapter bar stops being readable. */
export const MIN_PANEL_WIDTH = 280;
/** Left for the conversation, so a drag can never squeeze it away. */
const MIN_CHAT_WIDTH = 360;

/** Keep a requested width inside what the window can actually give it. */
export function clampPanelWidth(px: number): number {
  const room =
    typeof window === "undefined"
      ? Number.POSITIVE_INFINITY
      : Math.max(MIN_PANEL_WIDTH, window.innerWidth - MIN_CHAT_WIDTH);
  return Math.round(Math.min(Math.max(px, MIN_PANEL_WIDTH), room));
}

/**
 * Where a turn has got to. One send is four separate waits, and they are not
 * alike: the upload is seconds, detection is the worker's minutes, and the
 * reply is the chat model generating on whatever hardware it has. Reporting all
 * four as "running detection" made a finished analysis look like a hung one —
 * the detectors were long done and it was Qwen still writing.
 */
export type TurnPhase =
  | "uploading"
  | "analysing"
  | "summarising"
  | "thinking"
  | "writing";

/** A turn in flight: where it has got to, and when it started. */
export interface TurnState {
  phase: TurnPhase;
  /** `Date.now()` at the send, so the wait can be counted out to the user. */
  startedAt: number;
}

/**
 * The streamed analysis of the attached video — the same pipeline the
 * standalone analyser uses. Frames arrive within a second or two of the upload,
 * well before the detection models finish, so the chat shows the actual footage
 * instead of a spinner.
 */
export interface LiveAnalysisState {
  jobId: string;
  /** False when no worker picked the job up (Redis/worker down). */
  queued: boolean;
  progress: number;
  stage: string | null;
  message: string;
  frames: JobFrame[];
  segments: JobSegment[];
  status: "running" | "done" | "failed";
  scanId?: number | null;
}

/**
 * One open SSE connection per session being analysed.
 *
 * These outlive the conversation being on screen on purpose: leaving a chat
 * used to close its stream, so a job that was halfway through went dark and the
 * turn waiting on it fell back to re-analysing the video from scratch.
 */
const subscriptions = new Map<number, JobSubscription>();

/** The streamed analysis of each session's current upload, awaited by `submit`. */
const pendingJobs = new Map<number, Promise<JobOutcome>>();

/** How the streamed analysis of an upload ended. */
interface JobOutcome {
  /** The scan the worker wrote, when it finished and produced one. */
  scanId: number | null;
  /** False when nothing usable came back and the chat must analyse itself. */
  ok: boolean;
}

/**
 * How long to wait on a silent job before analysing in the chat instead.
 *
 * A queued job with nobody consuming the queue never progresses, and waiting
 * on it forever would leave the conversation stuck on "Waiting for a worker".
 * `job.queued` doesn't count as a sign of life — it is replayed to every new
 * subscriber — so the short budget covers "is a worker even there", and the
 * long one covers a stage that reports nothing while it runs (the VLMs).
 */
const NO_WORKER_MS = 60_000;
const STALLED_MS = 300_000;

/**
 * Job states still worth watching when a conversation is reopened.
 *
 * A finished job has already written its outcome into the message history, so
 * resubscribing would only replay a card the transcript is showing anyway.
 * These two are the states where the answer is still coming.
 */
const UNFINISHED_JOB_STATUSES = new Set(["QUEUED", "RUNNING"]);

/**
 * The single moment worth showing from a finished analysis.
 *
 * An incident wins over a loose object detection, and within an incident the
 * peak second beats the start of the span — a merged span can cover the whole
 * clip, so its start is rarely the frame the violence is actually on. Returns
 * null when the detectors found nothing, which leaves the player where it is.
 */
function pickMoment(result?: ToolResult): Omit<PlaybackCommand, "nonce"> | null {
  if (!result || result.error) return null;

  const segment = [...(result.segments ?? [])].sort(
    (a, b) => (b.confidence ?? 0) - (a.confidence ?? 0)
  )[0];
  if (segment) {
    const start = segment.peak_second ?? segment.start_time;
    return {
      action: "play_segment",
      startTime: start,
      // Starting at the peak can land past the span's end, which would pause
      // the player on the same tick it started.
      endTime: Math.max(segment.end_time, start + 3),
      reason: `${segment.label} — the flagged moment`,
    };
  }

  const detection = [...(result.top_detections ?? [])].sort(
    (a, b) => (b.score ?? 0) - (a.score ?? 0)
  )[0];
  if (detection) {
    return {
      action: "seek",
      startTime: detection.second,
      reason: `${detection.label} appears here`,
    };
  }

  return null;
}

interface ChatState {
  sessions: ChatSession[];
  activeId: number | null;
  detail: ChatSessionDetail | null;
  health: ChatHealth | null;
  loading: boolean;
  /**
   * Sessions with a turn in flight, mapped to how that turn is going. A missing
   * entry means idle — a chat is only ever busy for itself.
   */
  busy: Record<number, TurnState>;
  /** Upload progress of the video being attached, by session id. */
  uploads: Record<number, number>;
  /** Live analysis of each session's attached video. */
  lives: Record<number, LiveAnalysisState>;
  /** Latest playback directive; the video panel consumes it. */
  playback: PlaybackCommand | null;
  /** Whether the video review panel is open. */
  panelOpen: boolean;
  /**
   * How wide that panel is, in pixels, on a side-by-side layout.
   *
   * The default column is deliberately narrow so the conversation stays
   * readable. Dragging the divider between the two, or the arrow in the panel
   * header, trades that away for a big player when you are actually watching
   * the footage. Stacked (narrow) layouts ignore this — there the panel is
   * always full width.
   */
  panelWidth: number;

  /** Load the session list + model status. Creates a first session if none exist. */
  init: () => Promise<void>;
  open: (id: number) => Promise<void>;
  newChat: () => Promise<void>;
  remove: (id: number) => Promise<void>;
  attach: (
    sessionId: number,
    file: File,
    options?: { model?: string; queries?: string }
  ) => Promise<string>;
  /** Follow a queued analysis job over SSE; resolves when it settles. */
  watchJob: (sessionId: number, jobId: string, queued: boolean) => Promise<JobOutcome>;
  /** Close the streams of every idle conversation (call on unmount). */
  stopStream: () => void;
  /** Drop everything — used on logout, so the next user starts clean. */
  reset: () => void;
  /**
   * Send the composer as one turn: upload and analyse the attached video (if
   * there is one), then put the question to the agent. This is what the arrow
   * button does — the attachment and the message travel together.
   */
  submit: (
    message: string,
    file?: File | null,
    options?: { model?: string; queries?: string }
  ) => Promise<void>;

  /** Issue a playback command directly (used by clicks in the transcript). */
  command: (cmd: Omit<PlaybackCommand, "nonce">) => void;
  togglePanel: () => void;
  /** Drag the divider: set an exact width (clamped to what fits). */
  setPanelWidth: (px: number) => void;
  /** The arrow: jump between the narrow default and the wide player. */
  togglePanelWidth: () => void;
}

export const useChatStore = create<ChatState>((set, get) => {
  /** Move a conversation to a stage, or `null` when its turn is over.
   *
   * The clock is set once, on the first stage, and carried through the rest:
   * the user is waiting on one send, not on four consecutive ones.
   */
  function setPhase(id: number, phase: TurnPhase | null): void {
    set((state) => {
      const busy = { ...state.busy };
      if (phase) {
        busy[id] = { phase, startedAt: state.busy[id]?.startedAt ?? Date.now() };
      } else {
        delete busy[id];
      }
      return { busy };
    });
  }

  /** Report upload progress for one session; null clears it. */
  function setUpload(id: number, percent: number | null): void {
    set((state) => {
      const uploads = { ...state.uploads };
      if (percent === null) delete uploads[id];
      else uploads[id] = percent;
      return { uploads };
    });
  }

  /** Merge a patch into one session's live analysis card, if it still has one. */
  function patchLive(id: number, patch: Partial<LiveAnalysisState>): void {
    set((state) => {
      const current = state.lives[id];
      if (!current) return {};
      return { lives: { ...state.lives, [id]: { ...current, ...patch } } };
    });
  }

  /** Append messages to a conversation, wherever the user happens to be.
   *
   * A turn started in chat A must land in chat A even if the user opened B
   * while it was thinking — that is the whole point of per-session state. Only
   * the loaded transcript is patched; a conversation that is not on screen has
   * the same messages waiting on the server when it is reopened.
   */
  function appendMessages(id: number, appended: ChatMessage[]): void {
    set((state) => {
      if (state.detail?.id !== id) return {};
      return {
        detail: { ...state.detail, messages: [...state.detail.messages, ...appended] },
      };
    });
  }

  /** Rewrite one message's text in place — how a streamed reply fills in. */
  function setMessageContent(id: number, messageId: number, content: string): void {
    set((state) => {
      if (state.detail?.id !== id) return {};
      return {
        detail: {
          ...state.detail,
          messages: state.detail.messages.map((message) =>
            message.id === messageId ? { ...message, content } : message
          ),
        },
      };
    });
  }

  /**
   * Put messages directly above an existing one.
   *
   * A tool card arrives while the reply beneath it is still being written, and
   * it describes work that happened first — so it belongs above that draft
   * rather than appended after it, which would order the transcript wrongly.
   */
  function insertMessagesBefore(
    id: number,
    messageId: number,
    inserted: ChatMessage[]
  ): void {
    set((state) => {
      if (state.detail?.id !== id) return {};
      const at = state.detail.messages.findIndex((m) => m.id === messageId);
      if (at === -1) return {};
      const messages = [...state.detail.messages];
      messages.splice(at, 0, ...inserted);
      return { detail: { ...state.detail, messages } };
    });
  }

  /**
   * Fold the video's readout into the transcript — the tool card with the exact
   * frames, then the written summary. The caller owns the busy flag.
   *
   * With a `scanId` this only writes up what the streamed job already found;
   * without one the backend runs the detectors itself. The video is never
   * analysed twice for a single upload.
   */
  async function runAnalysis(
    id: number,
    options: { scanId?: number | null; detector?: string; queries?: string } = {}
  ): Promise<void> {
    const turn = await analyzeVideo(id, options);

    const appended: ChatMessage[] = turn.tool_calls.map((call) => ({
      id: nextTempId(),
      role: "tool" as const,
      content: call.name,
      tool_name: call.name,
      tool_payload: { arguments: call.arguments, result: call.result },
      created_at: new Date().toISOString(),
    }));
    appended.push({
      id: nextTempId(),
      role: "assistant",
      content: turn.reply,
      created_at: new Date().toISOString(),
    });

    // Land the player on the incident itself, so the exact frame is on screen
    // by the time the user finishes reading the summary.
    const analysis = turn.tool_calls.find((c) => c.name === "analyze_video")?.result;
    const moment = pickMoment(analysis);

    appendMessages(id, appended);
    set((state) => {
      const patch: Partial<ChatState> = {};
      if (state.detail?.id === id && turn.scan_id) {
        patch.detail = { ...state.detail, last_scan_id: turn.scan_id };
      }
      // The player only belongs to the conversation on screen. Seeking it for a
      // chat the user has since left would jump the wrong video.
      if (state.activeId === id && moment) {
        patch.playback = { ...moment, nonce: ++commandNonce };
        // Surface the review panel so the user can immediately jump to moments.
        patch.panelOpen = true;
      }
      return patch;
    });
  }

  /**
   * Put one message to the agent and fold its reply — and the tools it ran —
   * into the transcript as they arrive. The caller owns the busy flag and the
   * user's bubble.
   *
   * The reply is streamed, so the bubble is created empty and rewritten as the
   * tokens land. What the stream shows is a preview: the turn's final `reply`
   * is the cleaned text the backend persisted, and that is what the bubble is
   * left holding.
   */
  async function runTurn(id: number, message: string): Promise<void> {
    // Rewritten on every delta, so its id has to stay stable for the whole turn.
    const draftId = nextTempId();
    let draft = "";
    let writing = false;
    appendMessages(id, [
      {
        id: draftId,
        role: "assistant",
        content: "",
        created_at: new Date().toISOString(),
      },
    ]);

    const turn = await streamMessage(id, message, (event) => {
      switch (event.type) {
        case "delta":
          draft += event.text;
          setMessageContent(id, draftId, draft);
          // There is visibly an answer now; "Thinking…" is no longer true.
          // Only on the first token — the phase must not be rewritten once
          // per token when the bubble above is already re-rendering.
          if (!writing) {
            writing = true;
            setPhase(id, "writing");
          }
          break;
        case "reset":
          // What was streamed turned out to be the preamble of a tool call, or
          // the backend dropped mid-sentence. Either way it is not the answer.
          draft = "";
          writing = false;
          setMessageContent(id, draftId, "");
          setPhase(id, "thinking");
          break;
        case "tool":
          insertMessagesBefore(id, draftId, [
            {
              id: nextTempId(),
              role: "tool" as const,
              content: event.name,
              tool_name: event.name,
              tool_payload: { arguments: event.arguments, result: event.result },
              created_at: new Date().toISOString(),
            },
          ]);
          break;
      }
    });

    // The deltas were the preview; this is the text the backend stored.
    setMessageContent(id, draftId, turn.reply);

    // The agent can drive the player; the last directive of the turn wins.
    const directive = [...turn.tool_calls]
      .reverse()
      .find((call) => call.result?.kind === "playback" && !call.result.error);

    set((state) => {
      const patch: Partial<ChatState> = {};
      if (state.detail?.id === id && turn.scan_id) {
        patch.detail = { ...state.detail, last_scan_id: turn.scan_id };
      }
      if (state.activeId === id && directive) {
        patch.playback = {
          action: directive.result.action as PlaybackCommand["action"],
          startTime: directive.result.start_time ?? undefined,
          endTime: directive.result.end_time ?? undefined,
          reason: directive.result.reason,
          nonce: ++commandNonce,
        };
        patch.panelOpen = true;
      }
      return patch;
    });
  }

  /** Drop a local notice into the transcript when a turn could not finish. */
  function noteFailure(id: number, text: string): void {
    appendMessages(id, [
      {
        id: nextTempId(),
        role: "assistant" as const,
        content: text,
        created_at: new Date().toISOString(),
      },
    ]);
  }

  /** Forget everything held for one conversation, and close its stream. */
  function forgetSession(id: number): void {
    subscriptions.get(id)?.close();
    subscriptions.delete(id);
    pendingJobs.delete(id);
    set((state) => {
      const busy = { ...state.busy };
      const uploads = { ...state.uploads };
      const lives = { ...state.lives };
      delete busy[id];
      delete uploads[id];
      delete lives[id];
      return { busy, uploads, lives };
    });
  }

  return {
    sessions: [],
    activeId: null,
    detail: null,
    health: null,
    loading: false,
    busy: {},
    uploads: {},
    lives: {},
    playback: null,
    panelOpen: true,
    panelWidth: DEFAULT_PANEL_WIDTH,

    command: (cmd) => set({ playback: { ...cmd, nonce: ++commandNonce } }),
    togglePanel: () => set((state) => ({ panelOpen: !state.panelOpen })),
    setPanelWidth: (px) => set({ panelWidth: clampPanelWidth(px) }),
    togglePanelWidth: () =>
      set((state) => ({
        panelWidth:
          state.panelWidth > (DEFAULT_PANEL_WIDTH + WIDE_PANEL_WIDTH) / 2
            ? DEFAULT_PANEL_WIDTH
            : clampPanelWidth(WIDE_PANEL_WIDTH),
      })),

    init: async () => {
      if (get().loading) return;
      set({ loading: true });
      try {
        const [rows, health] = await Promise.all([
          listSessions(),
          getChatHealth().catch(() => null),
        ]);
        set({ sessions: rows, health });

        if (rows.length === 0) {
          await get().newChat();
        } else if (get().activeId === null) {
          await get().open(rows[0].id);
        }
      } finally {
        set({ loading: false });
      }
    },

    open: async (id) => {
      // The player directive belongs to the video we are leaving; the streams
      // and live cards of other conversations keep running behind us.
      if (get().activeId !== id) set({ playback: null });
      set({ activeId: id });

      const detail = await getSession(id);
      // Guard against a slow response for a session the user already left.
      if (get().activeId !== id) return;
      set({ detail });

      // Rejoin an analysis that is still running. `watchJob` owns the live card
      // and the event subscription, so re-entering a conversation mid-run shows
      // real progress instead of a spinner frozen at whatever the previous page
      // load last saw — or nothing at all, once that tab was closed. A stream we
      // are already holding is left alone rather than restarted from zero.
      if (
        detail.job_id &&
        UNFINISHED_JOB_STATUSES.has(detail.job_status ?? "") &&
        !subscriptions.has(id)
      ) {
        set({ panelOpen: true });
        void get().watchJob(id, detail.job_id, true);
      }
    },

    newChat: async () => {
      // Nothing is torn down: a conversation left mid-thought keeps its turn,
      // its stream and its live card, and finishes in the background.
      set({ playback: null });
      const created = await createSession();
      set((state) => ({
        sessions: [created, ...state.sessions],
        activeId: created.id,
        detail: { ...created, messages: [] },
      }));
    },

    remove: async (id) => {
      await deleteSession(id);
      forgetSession(id);
      const remaining = get().sessions.filter((s) => s.id !== id);
      set({ sessions: remaining });

      if (get().activeId !== id) return;
      if (remaining.length > 0) {
        await get().open(remaining[0].id);
      } else {
        set({ activeId: null, detail: null });
        await get().newChat();
      }
    },

    attach: async (sessionId, file, options = {}) => {
      // Drop any stream still running for a previous upload in this chat.
      subscriptions.get(sessionId)?.close();
      subscriptions.delete(sessionId);
      pendingJobs.delete(sessionId);
      setUpload(sessionId, 0);
      set((state) => {
        const lives = { ...state.lives };
        delete lives[sessionId];
        return { lives };
      });

      try {
        const attached = await attachVideo(sessionId, file, options, (percent) =>
          setUpload(sessionId, percent)
        );
        const patch = {
          video_name: attached.video_name,
          video_url: attached.video_url,
        };
        set((state) => ({
          detail:
            state.detail?.id === sessionId
              ? { ...state.detail, ...patch }
              : state.detail,
          sessions: state.sessions.map((s) =>
            s.id === sessionId ? { ...s, ...patch } : s
          ),
          panelOpen: state.activeId === sessionId ? true : state.panelOpen,
        }));

        // The job is the only thing that analyses this upload; `submit` waits
        // on the promise so the summary describes what the worker found.
        if (attached.job_id) {
          pendingJobs.set(
            sessionId,
            get().watchJob(sessionId, attached.job_id, attached.queued)
          );
        }
        return attached.video_name;
      } finally {
        setUpload(sessionId, null);
      }
    },

    watchJob: (sessionId, jobId, queued) => {
      set((state) => ({
        lives: {
          ...state.lives,
          [sessionId]: {
            jobId,
            queued,
            progress: 0,
            stage: "queued",
            message: queued
              ? "Queued for analysis…"
              : "Stored, but no worker is listening — start it with: python -m arq worker.WorkerSettings",
            frames: [],
            segments: [],
            status: "running",
          },
        },
      }));

      return new Promise<JobOutcome>((resolve) => {
        let settled = false;
        let timer: ReturnType<typeof setTimeout>;
        /** This run's stream, so a superseded run never closes its successor. */
        let handle: JobSubscription | null = null;

        const settle = (outcome: JobOutcome) => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          resolve(outcome);
        };

        /** Drop this run's stream — but only while it is still the live one. */
        const release = () => {
          if (subscriptions.get(sessionId) === handle) subscriptions.delete(sessionId);
          handle?.close();
        };

        /**
         * Update the card only while it is still showing THIS job.
         *
         * Replacing a video re-attaches and starts a second run against the same
         * session; a slow event from the abandoned one must not paint over the
         * new one's progress.
         */
        const patchMine = (patch: Partial<LiveAnalysisState>) => {
          if (get().lives[sessionId]?.jobId !== jobId) return;
          patchLive(sessionId, patch);
        };

        /** Restart the patience clock; a silent job eventually gives up. */
        const wait = (ms: number) => {
          // Already given up (or finished): a late-waking worker still updates
          // the card, but must not re-arm a clock that declares it dead.
          if (settled) return;
          clearTimeout(timer);
          timer = setTimeout(() => {
            patchMine({
              status: "failed",
              message:
                "No worker is processing this — analysing it here instead. " +
                "Start one with: python -m arq worker.WorkerSettings",
            });
            release();
            settle({ scanId: null, ok: false });
          }, ms);
        };

        // Nothing was enqueued, so nothing will ever run it.
        if (!queued) {
          settle({ scanId: null, ok: false });
        } else {
          wait(NO_WORKER_MS);
        }

        handle = subscribeToJob(jobId, {
          onEvent: (event) => {
            // `job.queued` is replayed to every subscriber, so it says nothing
            // about whether a worker exists.
            if (event.event !== "job.queued") wait(STALLED_MS);
            patchMine({
              progress: event.progress ?? 0,
              stage: event.stage,
              ...(event.message ? { message: event.message } : {}),
            });
          },
          // Frames render the moment the worker announces them, long before
          // the detectors finish — this is what makes the wait legible.
          onFrame: (frame) =>
            set((state) => {
              const live = state.lives[sessionId];
              if (!live || live.jobId !== jobId) return {};
              if (live.frames.some((f) => f.id === frame.id)) return {};
              return {
                lives: {
                  ...state.lives,
                  [sessionId]: { ...live, frames: [...live.frames, frame] },
                },
              };
            }),
          onSegment: (segment) =>
            set((state) => {
              const live = state.lives[sessionId];
              if (!live || live.jobId !== jobId) return {};
              if (live.segments.some((s) => s.id === segment.id)) return {};
              return {
                lives: {
                  ...state.lives,
                  [sessionId]: { ...live, segments: [...live.segments, segment] },
                },
              };
            }),
          onDone: async (event) => {
            // The stream closes itself on a terminal event; just stop tracking it.
            if (subscriptions.get(sessionId) === handle) subscriptions.delete(sessionId);
            if (event.event === "job.failed") {
              patchMine({
                status: "failed",
                message: event.message ?? "Analysis failed.",
              });
              settle({ scanId: null, ok: false });
              return;
            }
            patchMine({ status: "done", progress: 100 });
            try {
              // Captions are written at the very end of the pipeline.
              const snapshot = await getJob(jobId);
              patchMine({ frames: snapshot.frames, scanId: snapshot.scan_id });
              // Without a scan there is nothing to write up, so let the chat
              // fall back to detecting rather than report an empty analysis.
              settle({ scanId: snapshot.scan_id, ok: snapshot.scan_id !== null });
            } catch {
              /* the stream already told us it succeeded */
              settle({ scanId: null, ok: false });
            }
          },
          onError: (message) => patchMine({ message }),
        });
        subscriptions.set(sessionId, handle);
      });
    },

    stopStream: () => {
      // A conversation still mid-turn keeps its stream: the turn is waiting on
      // that job, and cutting it would make it fall back to re-analysing.
      const { busy } = get();
      for (const [id, sub] of [...subscriptions]) {
        if (busy[id]) continue;
        sub.close();
        subscriptions.delete(id);
      }
    },

    reset: () => {
      for (const sub of subscriptions.values()) sub.close();
      subscriptions.clear();
      pendingJobs.clear();
      set({
        sessions: [],
        activeId: null,
        detail: null,
        health: null,
        loading: false,
        busy: {},
        uploads: {},
        lives: {},
        playback: null,
      });
    },

    submit: async (message, file, options = {}) => {
      const id = get().activeId;
      // Only this conversation's own turn blocks it; the others carry on.
      if (id === null || get().busy[id]) return;

      const text = message.trim();
      if (!text && !file) return;

      // Show the question straight away — the upload, the detectors and the
      // agent's own inference all run inline below it.
      const optimistic: ChatMessage | null = text
        ? {
            id: nextTempId(),
            role: "user",
            content: text,
            created_at: new Date().toISOString(),
          }
        : null;

      setPhase(id, file ? "uploading" : "thinking");
      if (optimistic) appendMessages(id, [optimistic]);

      try {
        if (file) {
          // One upload, one analysis: the queued job is what runs the models —
          // frames appear within a second or two — and the file stays on disk
          // for the agent's tools and the review player.
          await get().attach(id, file, options);
          setPhase(id, "analysing");
          const outcome = await pendingJobs.get(id);
          pendingJobs.delete(id);
          // The written readout — what was found, at which timestamp, and the
          // still captured at each flagged moment. It describes the job's scan;
          // only if the job produced none does the backend detect for itself.
          setPhase(id, "summarising");
          await runAnalysis(id, {
            scanId: outcome?.ok ? outcome.scanId : null,
            detector: options.model,
            queries: options.queries,
          });
        }
        // Detection is over by here. What follows is the chat model composing a
        // reply, which on local hardware is usually the longest wait of the two.
        if (text) {
          setPhase(id, "thinking");
          await runTurn(id, text);
        }

        // The first message titles the session — refresh so the sidebar matches.
        const rows = await listSessions().catch(() => null);
        if (rows) set({ sessions: rows });
      } catch (error) {
        noteFailure(
          id,
          file
            ? "I could not finish that — the upload or the detectors failed. Try sending it again."
            : "I could not answer that — check the backend is running, then try again."
        );
        throw error;
      } finally {
        setPhase(id, null);
      }
    },
  };
});

/**
 * Whether the conversation currently on screen is mid-turn.
 *
 * Components want "can I type here?", not "is anything running anywhere" —
 * asking per session is what lets a second chat stay usable while the first
 * one thinks.
 */
export const useIsBusy = () =>
  useChatStore((s) => (s.activeId !== null ? Boolean(s.busy[s.activeId]) : false));

/** The open conversation's turn — its stage and start — or null when idle. */
export const useTurnState = () =>
  useChatStore((s) => (s.activeId !== null ? (s.busy[s.activeId] ?? null) : null));

/** Upload progress of the open conversation, or null when nothing is uploading. */
export const useUploadPercent = () =>
  useChatStore((s) => (s.activeId !== null ? (s.uploads[s.activeId] ?? null) : null));

/** The streamed analysis of the open conversation, if it has one. */
export const useLiveAnalysis = () =>
  useChatStore((s) => (s.activeId !== null ? (s.lives[s.activeId] ?? null) : null));
