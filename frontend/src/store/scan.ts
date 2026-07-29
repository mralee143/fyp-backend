import { create } from "zustand";
import type {
  VideoDetectionResponse,
  LlmDetectionResponse,
} from "@/lib/detection";

interface ScanState {
  /** Object URL of the uploaded video (blob:) — valid for this session only. */
  videoUrl: string | null;
  videoName: string | null;
  /** Object-detection result (YOLO/OWLv2), if that model was used. */
  objectResult: VideoDetectionResponse | null;
  /** LLM (Gemini) violence result, if that model was used. */
  llmResult: LlmDetectionResponse | null;

  setObjectScan: (
    videoUrl: string,
    videoName: string,
    result: VideoDetectionResponse
  ) => void;
  setLlmScan: (
    videoUrl: string,
    videoName: string,
    result: LlmDetectionResponse
  ) => void;
  clear: () => void;
}

/**
 * Holds the most recent scan so the results page can render it after
 * client-side navigation. Not persisted — the blob URL only lives while the
 * page is open.
 */
export const useScanStore = create<ScanState>((set, get) => ({
  videoUrl: null,
  videoName: null,
  objectResult: null,
  llmResult: null,

  setObjectScan: (videoUrl, videoName, result) => {
    const prev = get().videoUrl;
    if (prev && prev !== videoUrl) URL.revokeObjectURL(prev);
    set({ videoUrl, videoName, objectResult: result, llmResult: null });
  },
  setLlmScan: (videoUrl, videoName, result) => {
    const prev = get().videoUrl;
    if (prev && prev !== videoUrl) URL.revokeObjectURL(prev);
    set({ videoUrl, videoName, llmResult: result, objectResult: null });
  },
  clear: () => {
    const prev = get().videoUrl;
    if (prev) URL.revokeObjectURL(prev);
    set({ videoUrl: null, videoName: null, objectResult: null, llmResult: null });
  },
}));
