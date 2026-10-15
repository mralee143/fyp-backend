import type { AxiosProgressEvent } from "axios";
import { api } from "@/lib/api";
import { getToken, useAuthStore } from "@/store/auth";

/**
 * Client for the Qwen agent chatbot (`/chat` on the backend).
 *
 * A session owns one uploaded video. Messages sent to it are answered by the
 * orchestrator agent, which may call tools along the way — video analysis
 * (YOLO and friends) and the database-insights agent. Those calls come back in
 * `tool_calls` so the UI can show the agent's work.
 */

/** One conversation in the sidebar. */
export interface ChatSession {
  id: number;
  title: string;
  video_name?: string | null;
  video_url?: string | null;
  last_scan_id?: number | null;
  message_count: number;
  created_at: string;
  updated_at: string;
}

/** One stored message. `tool` rows carry the payload of a single tool call. */
export interface ChatMessage {
  id: number;
  role: "user" | "assistant" | "tool";
  content: string;
  tool_name?: string | null;
  tool_payload?: { arguments?: Record<string, unknown>; result?: ToolResult } | null;
  created_at: string;
}

export interface ChatSessionDetail {
  id: number;
  title: string;
  video_name?: string | null;
  video_url?: string | null;
  last_scan_id?: number | null;
  /**
   * Analysis job queued for the attached video. The server persists it, so a
   * conversation reopened in a new page load can rejoin a run that is still
   * going — the handle no longer dies with the tab that started the upload.
   */
  job_id?: string | null;
  /** Live status of `job_id`: QUEUED | RUNNING | SUCCEEDED | FAILED | CANCELLED. */
  job_status?: string | null;
  job_progress?: number | null;
  created_at: string;
  updated_at: string;
  messages: ChatMessage[];
}

/** Compacted detection payload the agent hands back from a tool. */
export interface ToolResult {
  error?: string;
  kind?: "object_detection" | "incident_analysis" | "playback" | "frame";
  detector?: string;
  scan_id?: number | null;
  model_id?: string;
  video?: string | null;
  /* object_detection */
  weapon_detected?: boolean;
  frames_scanned?: number;
  detection_count?: number;
  label_counts?: Record<string, number>;
  top_detections?: {
    timestamp: string;
    second: number;
    label: string;
    score: number;
    /** Still captured at this detection, when the backend could grab one. */
    frame_url?: string | null;
    frame_second?: number;
    /** Detector box, and where in the frame it sits, in words. */
    box_xyxy?: number[] | null;
    location?: string;
  }[];
  truncated?: boolean;
  /* incident_analysis */
  violence_detected?: boolean;
  summary?: string;
  segments?: {
    label: string;
    category: string;
    description: string;
    start_time: number;
    end_time: number;
    confidence: number;
    /** The second the detector was most certain on — a merged span can cover
     *  the whole clip, so its midpoint means little. */
    peak_second?: number | null;
    clip_url?: string | null;
    /** Still captured mid-incident, when the backend could grab one. */
    frame_url?: string | null;
    frame_second?: number;
    /** Where in the frame the incident sits, in words. */
    location?: string;
  }[];
  /* query_detection_database */
  question?: string;
  sql?: string;
  row_count?: number;
  rows?: Record<string, unknown>[];
  insight?: string;
  /* list_recent_scans */
  scans?: Record<string, unknown>[];
  count?: number;
  /* control_video_playback — a directive the video panel carries out */
  action?: "play" | "pause" | "replay" | "seek" | "play_segment";
  start_time?: number | null;
  end_time?: number | null;
  reason?: string;
  /* show_frame_at — one captured still */
  frame_url?: string | null;
  second?: number;
  timestamp?: string;
  note?: string;
  location?: string;
}

export interface ToolCall {
  name: string;
  arguments: Record<string, unknown>;
  result: ToolResult;
}

export interface ChatTurn {
  reply: string;
  tool_calls: ToolCall[];
  scan_id?: number | null;
  error?: boolean;
}

export interface ChatHealth {
  online: boolean;
  model: string;
  base_url: string;
  local_fallback: boolean;
}

/** Human label for each tool, used on the "agent worked" cards. */
export const TOOL_LABELS: Record<string, string> = {
  analyze_video: "Analysed the video",
  query_detection_database: "Queried the detection database",
  control_video_playback: "Controlled the video player",
  show_frame_at: "Captured the frame",
  list_recent_scans: "Looked up recent scans",
  get_scan_report: "Opened a past scan report",
};

export async function getChatHealth(): Promise<ChatHealth> {
  const { data } = await api.get<ChatHealth>("/chat/health");
  return data;
}

export async function listSessions(): Promise<ChatSession[]> {
  const { data } = await api.get<ChatSession[]>("/chat/sessions");
  return data;
}

export async function createSession(): Promise<ChatSession> {
  const { data } = await api.post<ChatSession>("/chat/sessions");
  return data;
}

export async function getSession(id: number): Promise<ChatSessionDetail> {
  const { data } = await api.get<ChatSessionDetail>(`/chat/sessions/${id}`);
  return data;
}

export async function deleteSession(id: number): Promise<void> {
  await api.delete(`/chat/sessions/${id}`);
}

/** What attaching a video returns, including the streamed analysis it queued. */
export interface AttachedVideo {
  video_name: string;
  video_url: string;
  session_id: number;
  /** Job streaming the analysis; null when it could not be queued. */
  job_id: string | null;
  events_url: string | null;
  queued: boolean;
}

/**
 * Attach (or replace) the video a session's agent can analyse.
 *
 * One upload does two jobs: the file is kept locally for the agent's tools and
 * the review player, and the same copy is queued on the analysis pipeline — so
 * the chat gets live progress and streamed frames without sending the bytes
 * twice. Subscribe to the returned job with `subscribeToJob` from lib/jobs.
 */
export async function attachVideo(
  sessionId: number,
  file: File,
  options: { model?: string; queries?: string } = {},
  onProgress?: (percent: number) => void
): Promise<AttachedVideo> {
  const form = new FormData();
  form.append("file", file);
  form.append("model", options.model ?? "auto");
  if (options.model === "owlv2" && options.queries?.trim()) {
    form.append("queries", options.queries.trim());
  }

  const { data } = await api.post<AttachedVideo>(`/chat/sessions/${sessionId}/video`, form, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 0,
    onUploadProgress: (e: AxiosProgressEvent) => {
      if (onProgress && e.total) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    },
  });
  return data;
}

/**
 * Put the attached video's findings into the conversation, without analysing
 * it twice.
 *
 * Pass the `scanId` the streamed job produced and the backend simply writes
 * that scan up as the tool card and the summary. Omit it — no worker took the
 * job, or it failed — and the detectors run inline with `detector` instead.
 */
export async function analyzeVideo(
  sessionId: number,
  options: { scanId?: number | null; detector?: string; queries?: string } = {}
): Promise<ChatTurn> {
  const { data } = await api.post<ChatTurn>(
    `/chat/sessions/${sessionId}/analyze`,
    {
      scan_id: options.scanId ?? null,
      detector: options.detector ?? null,
      queries: options.queries ?? null,
    },
    { timeout: 0 }
  );
  return data;
}

/**
 * Send a message and wait for the agent's reply.
 * A turn can run model inference inline, so there is no client-side timeout.
 */
export async function sendMessage(
  sessionId: number,
  message: string
): Promise<ChatTurn> {
  const { data } = await api.post<ChatTurn>(
    `/chat/sessions/${sessionId}/messages`,
    { message },
    { timeout: 0 }
  );
  return data;
}

/** One frame from the streaming turn endpoint. */
export type ChatStreamEvent =
  | { type: "delta"; text: string }
  | { type: "reset" }
  | { type: "tool_started"; name: string }
  | {
      type: "tool";
      name: string;
      arguments: Record<string, unknown>;
      result: ToolResult;
    }
  | { type: "done"; result: ChatTurn };

/**
 * Send a message and receive the reply as it is written.
 *
 * The same turn as {@link sendMessage} — it persists the same rows and ends
 * with the same payload — but the text arrives token by token instead of after
 * the whole thing is generated. That wait is minutes on local weights, so this
 * is what makes the chat feel answered rather than hung.
 *
 * `onEvent` sees every frame; the resolved value is the authoritative turn from
 * the final `done` frame. Treat the streamed deltas as a live preview and the
 * returned `reply` as the text to keep — it is the cleaned, persisted version.
 *
 * Uses `fetch` rather than the shared axios instance because only `fetch`
 * exposes the response body as a stream; the auth header and the 401 handling
 * the interceptors would have applied are done here instead.
 */
export async function streamMessage(
  sessionId: number,
  message: string,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal
): Promise<ChatTurn> {
  const baseURL = api.defaults.baseURL ?? "";
  const token = getToken();

  const response = await fetch(
    `${baseURL}/chat/sessions/${sessionId}/messages/stream`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ message }),
      signal,
    }
  );

  if (response.status === 401) {
    useAuthStore.getState().clearAuth();
    if (typeof window !== "undefined" && window.location.pathname !== "/login") {
      window.location.href = "/login";
    }
    throw new Error("Session expired.");
  }
  if (!response.ok || !response.body) {
    throw new Error(`The agent stream failed (HTTP ${response.status}).`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let final: ChatTurn | null = null;

  // SSE frames are separated by a blank line, and a single read can land
  // mid-frame, so whatever follows the last blank line is kept for next time.
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");

    let split: number;
    while ((split = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      for (const line of frame.split("\n")) {
        // Anything else is a comment — the server's keepalive, which exists
        // only to hold the connection open while the model is still silent.
        if (!line.startsWith("data:")) continue;
        const payload = line.slice("data:".length).trim();
        if (!payload) continue;
        let event: ChatStreamEvent;
        try {
          event = JSON.parse(payload) as ChatStreamEvent;
        } catch {
          continue;
        }
        if (event.type === "done") final = event.result;
        onEvent(event);
      }
    }
  }

  if (!final) {
    throw new Error("The reply stream ended before the agent finished.");
  }
  return final;
}
