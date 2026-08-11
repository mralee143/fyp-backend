import type { AxiosProgressEvent } from "axios";
import { api } from "@/lib/api";

/** One object detected in a sampled frame (mirrors backend schema). */
export interface Detection {
  second: number;
  timestamp: string;
  label: string;
  score: number;
  box_xyxy: [number, number, number, number];
}

/** Full response from POST /detection/video. */
export interface VideoDetectionResponse {
  model_id: string;
  queries: string[];
  score_threshold: number;
  frames_scanned: number;
  detection_count: number;
  label_counts: Record<string, number>;
  weapon_detected: boolean;
  detections: Detection[];
}

export interface DetectOptions {
  model: "yolo" | "owlv2";
  queries?: string;
  numFrames: number;
  threshold: number;
}

/** One violence event found by the LLM (mirrors backend ViolenceSegment). */
export interface ViolenceSegment {
  /** Set on stored scans; absent on the direct (unsaved) detection routes. */
  id?: number;
  /** Position within the scan — how the chat agent addresses one incident. */
  ordinal?: number;
  label: string;
  /** "violence" | "theft" | "harassment" | "other" — backend may add more. */
  category: string;
  description: string;
  start_time: number;
  end_time: number;
  confidence: number;
  clip_url?: string | null;
  annotated_clip_url?: string | null;
  explanation?: string | null;
}

/** Response from POST /detection/video/llm (Gemini analysis). */
export interface LlmDetectionResponse {
  model_id: string;
  violence_detected: boolean;
  summary: string;
  segments: ViolenceSegment[];
}

/** Gemini inline limit — keep LLM uploads small. */
export const MAX_LLM_BYTES = 200 * 1024 * 1024; // 200 MB (Gemini Files API)

/** Turn a backend media path ("/media/clips/x.mp4") into a full URL. */
export function mediaUrl(path?: string | null): string | undefined {
  if (!path) return undefined;
  if (path.startsWith("http")) return path;
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8010";
  return `${base}${path}`;
}

export const MAX_VIDEO_BYTES = 200 * 1024 * 1024; // 200 MB
export const ALLOWED_VIDEO_EXT = [".mp4", ".mov", ".avi", ".mkv", ".webm"];

/**
 * Upload a video to the detection endpoint.
 * `onProgress` reports upload progress (0-100); inference runs after upload.
 */
export async function detectVideo(
  file: File,
  opts: DetectOptions,
  onProgress?: (percent: number) => void
): Promise<VideoDetectionResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("model", opts.model);
  if (opts.model === "owlv2" && opts.queries?.trim()) {
    form.append("queries", opts.queries.trim());
  }
  form.append("num_frames", String(opts.numFrames));
  form.append("threshold", String(opts.threshold));

  const { data } = await api.post<VideoDetectionResponse>(
    "/detection/video",
    form,
    {
      // Let the browser/axios set the multipart boundary; override the
      // instance's default application/json.
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 0, // model inference can take a while
      onUploadProgress: (e: AxiosProgressEvent) => {
        if (onProgress && e.total) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      },
    }
  );
  return data;
}

/**
 * Analyze a video for violence with the LLM (Gemini) endpoint.
 * `onProgress` reports upload progress (0-100); inference runs after upload.
 */
export async function detectVideoLlm(
  file: File,
  onProgress?: (percent: number) => void
): Promise<LlmDetectionResponse> {
  const form = new FormData();
  form.append("file", file);

  const { data } = await api.post<LlmDetectionResponse>(
    "/detection/video/llm",
    form,
    {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 0,
      onUploadProgress: (e: AxiosProgressEvent) => {
        if (onProgress && e.total) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      },
    }
  );
  return data;
}

/**
 * Recognise violent/criminal actions with the local model (no API key needed).
 * Returns the same shape as the LLM route, so results share one timeline view.
 */
export async function detectVideoAction(
  file: File,
  onProgress?: (percent: number) => void
): Promise<LlmDetectionResponse> {
  const form = new FormData();
  form.append("file", file);

  const { data } = await api.post<LlmDetectionResponse>(
    "/detection/video/action",
    form,
    {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 0,
      onUploadProgress: (e: AxiosProgressEvent) => {
        if (onProgress && e.total) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      },
    }
  );
  return data;
}

/**
 * Analyze a video with the local Qwen2.5-VL model (offline, no key/quota).
 * Slow on CPU — inference can take a few minutes.
 */
export async function detectVideoQwen(
  file: File,
  onProgress?: (percent: number) => void
): Promise<LlmDetectionResponse> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await api.post<LlmDetectionResponse>(
    "/detection/video/qwen",
    form,
    {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 0,
      onUploadProgress: (e: AxiosProgressEvent) => {
        if (onProgress && e.total) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      },
    }
  );
  return data;
}

/**
 * Auto detection — the backend runs all models in a cascade (Gemini → action →
 * Qwen) and returns the first that finds an incident. Best coverage.
 */
export async function detectVideoAuto(
  file: File,
  onProgress?: (percent: number) => void
): Promise<LlmDetectionResponse> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await api.post<LlmDetectionResponse>(
    "/detection/video/auto",
    form,
    {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 0,
      onUploadProgress: (e: AxiosProgressEvent) => {
        if (onProgress && e.total) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      },
    }
  );
  return data;
}

/** Aggregate scan counts for the dashboard. */
export interface ScanStats {
  total: number;
  threats: number;
  clear: number;
}

/** One past scan (metadata) for the history list. */
export interface ScanHistoryItem {
  id: number;
  filename: string;
  model: string;
  violence_detected: boolean;
  summary: string;
  created_at: string;
}

export async function getStats(): Promise<ScanStats> {
  const { data } = await api.get<ScanStats>("/detection/stats");
  return data;
}

export async function getHistory(): Promise<ScanHistoryItem[]> {
  const { data } = await api.get<ScanHistoryItem[]>("/detection/history");
  return data;
}

/** One image stored for a scan's video — a sampled frame or an incident still. */
export interface ScanImage {
  id: number;
  kind: "FRAME" | "ANNOTATED_FRAME" | "THUMBNAIL" | "UPLOAD";
  sequence: number | null;
  captured_at_seconds: number | null;
  /** Per-image summary written while the video was processed. */
  caption: string | null;
  width: number | null;
  height: number | null;
  segment_id: number | null;
  url: string | null;
}

/** The analysed video itself — what the report timeline is measured against. */
export interface ScanVideo {
  id: number;
  filename: string;
  /** Null when the probe failed; the timeline falls back to the last incident. */
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  url: string | null;
}

/** Full detail of one scan, for the report page. */
export interface ScanDetail {
  id: number;
  filename: string;
  model: string;
  violence_detected: boolean;
  summary: string;
  created_at: string;
  video?: ScanVideo;
  /** Every frame extracted from the video, newest analysis first. */
  images?: ScanImage[];
  /** Labels this scan touched, from the scan_labels join. */
  labels?: string[];
  result: {
    model_id?: string;
    summary?: string;
    segments?: ViolenceSegment[];
    detections?: Detection[];
  };
}

export async function getScan(id: number): Promise<ScanDetail> {
  const { data } = await api.get<ScanDetail>(`/detection/scan/${id}`);
  return data;
}

/** Client-side validation mirroring the backend limits. */
export function validateVideo(file: File): string | null {
  const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  if (!ALLOWED_VIDEO_EXT.includes(ext)) {
    return `Unsupported type. Allowed: ${ALLOWED_VIDEO_EXT.join(", ")}`;
  }
  if (file.size > MAX_VIDEO_BYTES) {
    return "Video exceeds the 200 MB limit.";
  }
  return null;
}
