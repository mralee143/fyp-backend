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
  label: string;
  /** "violence" | "theft" | "harassment" | "other" — backend may add more. */
  category: string;
  description: string;
  start_time: number;
  end_time: number;
  confidence: number;
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
