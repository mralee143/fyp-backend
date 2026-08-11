import { api } from "@/lib/api";

/**
 * Client for the per-analysis chat agent.
 *
 * Each finished scan has its own conversation, grounded in that scan's stored
 * analysis and extracted frames. This is separate from `@/lib/chat`, which
 * drives the general tool-using agent over an ad-hoc upload.
 */

export interface ChatCitation {
  segment_id?: number;
  ordinal?: number;
  label?: string;
  start_time?: number;
  end_time?: number;
  image_id?: number;
  sequence?: number;
  captured_at_seconds?: number;
  url?: string | null;
}

export interface ChatMessage {
  id: number;
  ordinal: number;
  role: "user" | "assistant" | "system";
  content: string;
  latency_ms: number | null;
  created_at: string | null;
  citations: { segment_id: number | null; image_id: number | null }[];
}

export interface Conversation {
  session_id: string;
  scan_id: number;
  messages: ChatMessage[];
  /** Openers tailored to what this particular analysis found. */
  suggestions: string[];
  frame_count: number;
  incident_count: number;
}

export interface ChatAnswer {
  session_id: string;
  message_id: number;
  answer: string;
  /** False when the analysis does not cover what was asked. */
  grounded: boolean;
  citations: ChatCitation[];
  latency_ms: number;
  /** Which backend answered: "gemini", "qwen" or "analysis". */
  source: string;
}

/** The one frame the agent picked out of an incident. */
export interface KeyFrame {
  image_id: number;
  second: number;
  /** Frame number in the source video (second × fps); null if fps is unknown. */
  frame_index: number | null;
  /** Human form, e.g. "0:52.30 (frame #1569)". */
  label: string;
  url: string | null;
}

/** A reading of one flagged incident, with the frame that shows it. */
export interface IncidentAnalysis extends ChatAnswer {
  /** What was actually asked — the agent's own prompt when none was given. */
  question: string;
  segment: {
    id: number;
    ordinal: number;
    label: string;
    category: string;
    start_time: number;
    end_time: number;
    confidence: number;
    clip_url: string | null;
    annotated_clip_url: string | null;
  };
  key_frame: KeyFrame | null;
  frames_examined: number;
}

export async function getConversation(scanId: number): Promise<Conversation> {
  const { data } = await api.get<Conversation>(`/detection/scans/${scanId}/chat`);
  return data;
}

export async function askQuestion(
  scanId: number,
  message: string
): Promise<ChatAnswer> {
  const { data } = await api.post<ChatAnswer>(
    `/detection/scans/${scanId}/chat/messages`,
    { message },
    { timeout: 0 } // a cold local model can take a while to answer
  );
  return data;
}

/**
 * Hand one flagged clip to the agent.
 *
 * Without a `message` the agent gives its standard breakdown — what happens,
 * which frame shows it, and whether the footage supports the label. The
 * exchange joins the scan's conversation, so it can be followed up normally.
 *
 * The first call on an older scan also extracts the incident's stills, which
 * can take a few seconds.
 */
export async function explainIncident(
  scanId: number,
  ordinal: number,
  message?: string
): Promise<IncidentAnalysis> {
  const { data } = await api.post<IncidentAnalysis>(
    `/detection/scans/${scanId}/chat/incidents/${ordinal}`,
    { message: message ?? null },
    { timeout: 0 }
  );
  return data;
}

export async function clearConversation(scanId: number): Promise<void> {
  await api.delete(`/detection/scans/${scanId}/chat`);
}
