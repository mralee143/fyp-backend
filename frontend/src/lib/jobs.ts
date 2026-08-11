import type { AxiosProgressEvent } from "axios";
import { api } from "@/lib/api";
import { getToken } from "@/store/auth";

/**
 * Client for the asynchronous analysis pipeline.
 *
 * `createJob` returns as soon as the upload is stored — the models have not run
 * yet. Progress arrives by push on `subscribeToJob`, which streams the same
 * events the backend POSTs to registered webhooks: frames within a second or
 * two, then incidents, then completion.
 */

export type JobStatusName =
  | "QUEUED"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELLED";

export type DetectionModel =
  | "auto"
  | "yolo"
  | "owlv2"
  | "llm"
  | "qwen"
  | "action";

/** One frame extracted from the video, with its per-image summary. */
export interface JobFrame {
  id: number;
  kind: "FRAME" | "ANNOTATED_FRAME" | "THUMBNAIL" | "UPLOAD";
  sequence: number | null;
  captured_at_seconds: number | null;
  caption: string | null;
  width: number | null;
  height: number | null;
  segment_id: number | null;
  url: string | null;
}

/** One incident, as delivered on a `segment.ready` event. */
export interface JobSegment {
  id: number;
  ordinal: number;
  label: string;
  category: string;
  description: string;
  explanation: string | null;
  start_time: number;
  end_time: number;
  confidence: number;
  clip_url: string | null;
  annotated_clip_url: string | null;
}

/** 202 response from POST /detection/jobs. */
export interface JobCreated {
  job_id: string;
  video_id: number;
  status: JobStatusName;
  model: string;
  filename: string;
  queued: boolean;
  reused_video: boolean;
  events_url: string;
}

/** Snapshot from GET /detection/jobs/{id}. */
export interface JobSnapshot {
  job_id: string;
  status: JobStatusName;
  stage: string | null;
  progress: number;
  model: string;
  filename: string;
  error: string | null;
  queued_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  scan_id: number | null;
  video_id: number;
  frame_count: number;
  last_sequence: number;
  frames: JobFrame[];
}

/** One pushed event. `image` / `segment` are present on the matching types. */
export interface JobEvent {
  id: number;
  sequence: number;
  event:
    | "job.queued"
    | "job.started"
    | "job.progress"
    | "frame.ready"
    | "segment.ready"
    | "job.completed"
    | "job.failed";
  job_id: string;
  status: JobStatusName;
  stage: string | null;
  progress: number;
  message: string | null;
  created_at: string | null;
  image?: JobFrame;
  segment?: JobSegment;
}

export const MAX_VIDEO_BYTES = 200 * 1024 * 1024;
export const ALLOWED_VIDEO_EXT = [".mp4", ".mov", ".avi", ".mkv", ".webm"];

/** Client-side validation mirroring the backend limits. */
export function validateVideo(file: File): string | null {
  const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  if (!ALLOWED_VIDEO_EXT.includes(ext)) {
    return `Unsupported type. Allowed: ${ALLOWED_VIDEO_EXT.join(", ")}`;
  }
  if (file.size > MAX_VIDEO_BYTES) return "Video exceeds the 200 MB limit.";
  return null;
}

export interface CreateJobOptions {
  model?: DetectionModel;
  queries?: string;
  numFrames?: number;
  threshold?: number;
}

/**
 * Upload a video and queue it for analysis.
 *
 * Resolves once the bytes are stored, typically about a second — not when the
 * analysis finishes. `onProgress` reports upload percentage only.
 */
export async function createJob(
  file: File,
  options: CreateJobOptions = {},
  onProgress?: (percent: number) => void
): Promise<JobCreated> {
  const form = new FormData();
  form.append("file", file);
  form.append("model", options.model ?? "auto");
  if (options.model === "owlv2" && options.queries?.trim()) {
    form.append("queries", options.queries.trim());
  }
  if (options.numFrames) form.append("num_frames", String(options.numFrames));
  if (options.threshold !== undefined) {
    form.append("threshold", String(options.threshold));
  }

  const { data } = await api.post<JobCreated>("/detection/jobs", form, {
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

export async function getJob(jobId: string): Promise<JobSnapshot> {
  const { data } = await api.get<JobSnapshot>(`/detection/jobs/${jobId}`);
  return data;
}

export async function getJobFrames(jobId: string): Promise<JobFrame[]> {
  const { data } = await api.get<JobFrame[]>(`/detection/jobs/${jobId}/frames`);
  return data;
}

export async function listJobs(limit = 20): Promise<JobSnapshot[]> {
  const { data } = await api.get<JobSnapshot[]>("/detection/jobs", {
    params: { limit },
  });
  return data;
}

export async function retryJob(jobId: string): Promise<JobCreated> {
  const { data } = await api.post<JobCreated>(`/detection/jobs/${jobId}/retry`);
  return data;
}

export interface JobSubscription {
  /** Stop listening and release the connection. */
  close: () => void;
}

export interface SubscribeHandlers {
  onEvent?: (event: JobEvent) => void;
  onFrame?: (frame: JobFrame, event: JobEvent) => void;
  onSegment?: (segment: JobSegment, event: JobEvent) => void;
  onDone?: (event: JobEvent) => void;
  onError?: (message: string) => void;
}

/**
 * Subscribe to a job's live progress over Server-Sent Events.
 *
 * `EventSource` cannot set an Authorization header, so the JWT travels as a
 * query parameter — the backend accepts either. It also reconnects on its own
 * and replays `Last-Event-ID`, so a dropped connection resumes without gaps.
 *
 * Returns a handle whose `close()` must be called on unmount, otherwise the
 * browser keeps reconnecting to a finished job.
 */
export function subscribeToJob(
  jobId: string,
  handlers: SubscribeHandlers
): JobSubscription {
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  const token = getToken() ?? "";
  const url = `${base}/detection/jobs/${jobId}/events?token=${encodeURIComponent(token)}`;

  const source = new EventSource(url);
  let closed = false;

  const close = () => {
    if (!closed) {
      closed = true;
      source.close();
    }
  };

  const handle = (raw: MessageEvent) => {
    let event: JobEvent;
    try {
      event = JSON.parse(raw.data) as JobEvent;
    } catch {
      return;
    }

    handlers.onEvent?.(event);
    if (event.image) handlers.onFrame?.(event.image, event);
    if (event.segment) handlers.onSegment?.(event.segment, event);

    if (event.event === "job.completed" || event.event === "job.failed") {
      handlers.onDone?.(event);
      close();
    }
  };

  for (const name of [
    "job.queued",
    "job.started",
    "job.progress",
    "frame.ready",
    "segment.ready",
    "job.completed",
    "job.failed",
  ]) {
    source.addEventListener(name, handle as EventListener);
  }

  // The backend closes the stream cleanly once a job reaches a terminal state.
  source.addEventListener("stream.end", () => close());
  source.addEventListener("stream.error", ((raw: MessageEvent) => {
    handlers.onError?.(raw.data ?? "The progress stream failed.");
    close();
  }) as EventListener);

  source.onerror = () => {
    // EventSource retries automatically; only surface a hard failure.
    if (source.readyState === EventSource.CLOSED && !closed) {
      handlers.onError?.("Lost connection to the progress stream.");
    }
  };

  return { close };
}

/** Human label for a pipeline stage. */
export function stageLabel(stage: string | null): string {
  switch (stage) {
    case "queued":
      return "Waiting for a worker";
    case "downloading":
      return "Loading the video";
    case "extracting_frames":
      return "Extracting frames";
    case "detecting":
      return "Running detection";
    case "saving":
      return "Saving results";
    case "clipping":
      return "Cutting incident clips";
    case "captioning":
      return "Captioning frames";
    case "completed":
      return "Done";
    case "failed":
      return "Failed";
    default:
      return "Working";
  }
}

/** Seconds → m:ss, matching how the report labels its timeline. */
export function formatTime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !isFinite(seconds)) return "0:00";
  const minutes = Math.floor(seconds / 60);
  const rest = Math.floor(seconds % 60);
  return `${minutes}:${rest.toString().padStart(2, "0")}`;
}
