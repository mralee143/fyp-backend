import { create } from "zustand";

import {
  analyzeVideo,
  attachVideo,
  createSession,
  deleteSession,
  getChatHealth,
  getSession,
  listSessions,
  sendMessage,
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

/** The one open SSE connection; replaced whenever a new video is attached. */
let subscription: JobSubscription | null = null;

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
  sending: boolean;
  uploadPercent: number | null;
  /** Latest playback directive; the video panel consumes it. */
  playback: PlaybackCommand | null;
  /** Whether the video review panel is open. */
  panelOpen: boolean;
  /** Live analysis of the attached video, or null when none is running. */
  live: LiveAnalysisState | null;

  /** Load the session list + model status. Creates a first session if none exist. */
  init: () => Promise<void>;
  open: (id: number) => Promise<void>;
  newChat: () => Promise<void>;
  remove: (id: number) => Promise<void>;
  attach: (
    file: File,
    options?: { model?: string; queries?: string }
  ) => Promise<string>;
  /** Follow a queued analysis job over SSE. */
  watchJob: (jobId: string, queued: boolean) => void;
  /** Close the SSE connection (call on unmount).*/
  stopStream: () => void;
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
}

export const useChatStore = create<ChatState>((set, get) => {
  /**
   * Run the detectors over the attached video and fold the readout into the
   * transcript — the tool card with the exact frames, then the written summary.
   * The caller owns the `sending` flag.
   */
  async function runAnalysis(id: number): Promise<void> {
    const turn = await analyzeVideo(id);

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

    set((state) => ({
      detail: state.detail
        ? {
            ...state.detail,
            last_scan_id: turn.scan_id ?? state.detail.last_scan_id,
            messages: [...state.detail.messages, ...appended],
          }
        : state.detail,
      playback: moment ? { ...moment, nonce: ++commandNonce } : state.playback,
      // Surface the review panel so the user can immediately jump to moments.
      panelOpen: true,
    }));
  }

  /**
   * Put one message to the agent and fold its reply (and the tools it ran) into
   * the transcript. The caller owns the `sending` flag and the user's bubble.
   */
  async function runTurn(id: number, message: string): Promise<void> {
    const turn = await sendMessage(id, message);

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

    // The agent can drive the player; the last directive of the turn wins.
    const directive = [...turn.tool_calls]
      .reverse()
      .find((call) => call.result?.kind === "playback" && !call.result.error);

    set((state) => ({
      detail: state.detail
        ? {
            ...state.detail,
            last_scan_id: turn.scan_id ?? state.detail.last_scan_id,
            messages: [...state.detail.messages, ...appended],
          }
        : state.detail,
      playback: directive
        ? {
            action: directive.result.action as PlaybackCommand["action"],
            startTime: directive.result.start_time ?? undefined,
            endTime: directive.result.end_time ?? undefined,
            reason: directive.result.reason,
            nonce: ++commandNonce,
          }
        : state.playback,
      panelOpen: directive ? true : state.panelOpen,
    }));
  }

  /** Drop a local notice into the transcript when a turn could not finish. */
  function noteFailure(text: string): void {
    set((state) => ({
      detail: state.detail
        ? {
            ...state.detail,
            messages: [
              ...state.detail.messages,
              {
                id: nextTempId(),
                role: "assistant" as const,
                content: text,
                created_at: new Date().toISOString(),
              },
            ],
          }
        : state.detail,
    }));
  }

  return {
    sessions: [],
    activeId: null,
    detail: null,
    health: null,
    loading: false,
    sending: false,
    uploadPercent: null,
    playback: null,
    panelOpen: true,
    live: null,

    command: (cmd) => set({ playback: { ...cmd, nonce: ++commandNonce } }),
    togglePanel: () => set((state) => ({ panelOpen: !state.panelOpen })),

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
      set({ activeId: id });
      const detail = await getSession(id);
      // Guard against a slow response for a session the user already left.
      if (get().activeId === id) set({ detail });
    },

    newChat: async () => {
      const created = await createSession();
      set((state) => ({
        sessions: [created, ...state.sessions],
        activeId: created.id,
        detail: { ...created, messages: [] },
      }));
    },

    remove: async (id) => {
      await deleteSession(id);
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

    attach: async (file, options = {}) => {
      const id = get().activeId;
      if (!id) throw new Error("No conversation is open.");

      // Drop any stream still running for a previous upload.
      get().stopStream();
      set({ uploadPercent: 0, live: null });

      try {
        const attached = await attachVideo(id, file, options, (percent) =>
          set({ uploadPercent: percent })
        );
        const patch = {
          video_name: attached.video_name,
          video_url: attached.video_url,
        };
        set((state) => ({
          detail: state.detail ? { ...state.detail, ...patch } : state.detail,
          sessions: state.sessions.map((s) => (s.id === id ? { ...s, ...patch } : s)),
          panelOpen: true,
        }));

        if (attached.job_id) {
          get().watchJob(attached.job_id, attached.queued);
        }
        return attached.video_name;
      } finally {
        set({ uploadPercent: null });
      }
    },

    watchJob: (jobId, queued) => {
      set({
        live: {
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
      });

      const patchLive = (patch: Partial<LiveAnalysisState>) =>
        set((state) => (state.live ? { live: { ...state.live, ...patch } } : {}));

      subscription = subscribeToJob(jobId, {
        onEvent: (event) =>
          patchLive({
            progress: event.progress ?? 0,
            stage: event.stage,
            ...(event.message ? { message: event.message } : {}),
          }),
        // Frames render the moment the worker announces them, long before the
        // detectors finish — this is what makes the wait legible.
        onFrame: (frame) =>
          set((state) =>
            state.live && !state.live.frames.some((f) => f.id === frame.id)
              ? { live: { ...state.live, frames: [...state.live.frames, frame] } }
              : {}
          ),
        onSegment: (segment) =>
          set((state) =>
            state.live && !state.live.segments.some((s) => s.id === segment.id)
              ? { live: { ...state.live, segments: [...state.live.segments, segment] } }
              : {}
          ),
        onDone: async (event) => {
          if (event.event === "job.failed") {
            patchLive({ status: "failed", message: event.message ?? "Analysis failed." });
            return;
          }
          patchLive({ status: "done", progress: 100 });
          try {
            // Captions are written at the very end of the pipeline.
            const snapshot = await getJob(jobId);
            patchLive({ frames: snapshot.frames, scanId: snapshot.scan_id });
          } catch {
            /* the stream already told us it succeeded */
          }
        },
        onError: (message) => patchLive({ message }),
      });
    },

    stopStream: () => {
      subscription?.close();
      subscription = null;
    },

    submit: async (message, file, options = {}) => {
      const id = get().activeId;
      if (!id || get().sending) return;

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

      set((state) => ({
        sending: true,
        detail:
          optimistic && state.detail
            ? { ...state.detail, messages: [...state.detail.messages, optimistic] }
            : state.detail,
      }));

      try {
        if (file) {
          // One upload, two consumers: the streamed job (frames appear within a
          // second or two) and the agent's tools, which need the file on disk.
          await get().attach(file, options);
          // The written readout — what was found, at which timestamp, and the
          // still captured at each flagged moment.
          await runAnalysis(id);
        }
        if (text) await runTurn(id, text);

        // The first message titles the session — refresh so the sidebar matches.
        const rows = await listSessions().catch(() => null);
        if (rows) set({ sessions: rows });
      } catch (error) {
        noteFailure(
          file
            ? "I could not finish that — the upload or the detectors failed. Try sending it again."
            : "I could not answer that — check the backend is running, then try again."
        );
        throw error;
      } finally {
        set({ sending: false });
      }
    },
  };
});
