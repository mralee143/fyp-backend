import argparse
import json
import os
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import torch
from decord import VideoReader, cpu
from transformers import (
    AutoImageProcessor,
    AutoModelForVideoClassification,
    AutoProcessor,
    Qwen2_5_VLForConditionalGeneration,
)

HF_TOKEN = os.getenv("HF_TOKEN")
QWEN_MODEL_ID = os.getenv("QWEN_MODEL_ID", "Qwen/Qwen2.5-VL-7B-Instruct")
VIDEO_MODEL_ID = os.getenv(
    "VIDEO_MODEL_ID", "MCG-NJU/videomae-base-finetuned-kinetics"
)
OBJECT_MODEL_ID = os.getenv(
    "OBJECT_MODEL_ID", "google/owlv2-base-patch16-ensemble"
)

# Fine-tuned YOLOv8 weapon model (fixed classes: Gun/Knife/Grenade/Explosion).
# Set YOLO_MODEL_PATH to use a local .pt file instead of the Hub download.
YOLO_MODEL_REPO = os.getenv("YOLO_MODEL_REPO", "Subh775/Threat-Detection-YOLOv8n")
YOLO_MODEL_FILE = os.getenv("YOLO_MODEL_FILE", "weights/best.pt")
YOLO_MODEL_PATH = os.getenv("YOLO_MODEL_PATH")

# Open-vocabulary queries scanned for in every sampled frame. Because OWLv2 is
# zero-shot, this list can be extended with any object name without retraining.
DEFAULT_OBJECT_QUERIES = (
    "a gun",
    "a pistol",
    "a rifle",
    "a handgun",
    "a firearm",
    "a knife",
    "a blade",
    "a sword",
    "a machete",
    "a baseball bat",
    "an axe",
    "a hammer",
    "a person holding a weapon",
    "a person fighting",
    "blood",
    "a masked person",
)

VIOLENCE_KEYWORDS = (
    "assault",
    "attack",
    "beat",
    "fighting",
    "hit",
    "kick",
    "knife",
    "punch",
    "shoot",
    "shot",
    "violence",
    "violent",
    "weapon",
)


def clamp(min_value: int, value: int, max_value: int) -> int:
    return max(min_value, min(value, max_value))


def sample_frame_indices(
    start_frame: int, end_frame: int, num_frames: int
) -> np.ndarray:
    window_size = max(end_frame - start_frame, 0)
    if window_size <= 0:
        return np.array([], dtype=np.int64)
    if window_size <= num_frames:
        return np.arange(start_frame, end_frame, dtype=np.int64)
    return np.linspace(start_frame, end_frame - 1, num_frames, dtype=np.int64)


def load_window_frames(
    reader: VideoReader, start_frame: int, end_frame: int, num_frames: int
) -> np.ndarray:
    indices = sample_frame_indices(start_frame, end_frame, num_frames)
    if indices.size == 0:
        return np.empty((0,), dtype=np.float32)
    return reader.get_batch(indices.tolist()).asnumpy()


def compute_violence_score(
    labels: Sequence[str], probabilities: torch.Tensor
) -> float:
    label_probs = (
        (label.lower(), float(probabilities[index]))
        for index, label in enumerate(labels)
    )
    violence_probs = (
        prob for label, prob in label_probs if any(key in label for key in VIOLENCE_KEYWORDS)
    )
    return min(sum(violence_probs), 1.0)


def frame_to_seconds(frame_idx: int, fps: float) -> float:
    if fps <= 0:
        return 0.0
    return frame_idx / fps


def format_seconds(seconds: float) -> str:
    total = int(seconds)
    hrs = total // 3600
    mins = (total % 3600) // 60
    secs = total % 60
    return f"{hrs:02}:{mins:02}:{secs:02}"


def window_resolution(
    duration_seconds: float,
    window_seconds: float,
    stride_seconds: float,
    target_windows: int = 16,
) -> tuple[float, float]:
    """Scale the sliding window to the length of the clip.

    A 3-second window stepped 2 seconds at a time can only place an incident to
    the nearest two seconds, so on a short clip every window overlaps the
    incident and the whole video comes back flagged as one span. Short clips
    therefore get a finer window and a shorter stride — roughly
    ``target_windows`` of them — while anything long enough for the caller's
    settings keeps them unchanged.
    """
    if duration_seconds <= 0:
        return window_seconds, stride_seconds

    stride = min(stride_seconds, max(0.5, duration_seconds / target_windows))
    window = min(window_seconds, max(1.0, duration_seconds / 4))
    # A window shorter than the stride would leave gaps in the timeline.
    return max(window, stride), stride


def prune_to_peak(
    hits: list[dict], floor_margin: float = 0.05, spread_ratio: float = 0.5
) -> list[dict]:
    """Keep only the windows that carry the evidence, per label.

    Consecutive sliding windows overlap by construction, so merging joins every
    consecutive hit: one weak window either side of a real incident is enough to
    stretch its segment across the whole clip, which is how a timeline ends up a
    solid bar. This drops the shoulders of the run, keeping the seconds the
    model is most certain about.

    The cutoff sits ``spread_ratio`` of the way down from the best window to the
    weakest one of the same label, but never closer to the peak than
    ``floor_margin``. That is what makes it safe on both shapes of result: where
    one moment clearly stands out, only that moment survives; where the model is
    uniformly confident — a clip that really is one long incident — the spread
    is tiny, so everything survives and nothing is invented.
    """
    if not hits:
        return []

    peak: dict[str, float] = {}
    low: dict[str, float] = {}
    for hit in hits:
        label = hit["label"]
        score = float(hit["confidence"])
        peak[label] = max(peak.get(label, score), score)
        low[label] = min(low.get(label, score), score)

    cutoff = {
        label: top - max(floor_margin, spread_ratio * (top - low[label]))
        for label, top in peak.items()
    }
    return [hit for hit in hits if float(hit["confidence"]) >= cutoff[hit["label"]]]


# Most of a video a single incident may cover before it is narrowed to its
# peak — see `focus_on_peak`. Set MAX_INCIDENT_COVERAGE=1 to report every span
# exactly as the detector classified it.
MAX_INCIDENT_COVERAGE = float(os.getenv("MAX_INCIDENT_COVERAGE", "0.8"))

# How close a span's weakest and strongest window must score before we treat the
# model as uniformly confident across it and leave the span at its full width.
# 0 restores the old behaviour: narrow every long span to its peak.
UNIFORM_SPREAD = float(os.getenv("INCIDENT_UNIFORM_SPREAD", "0.15"))


def video_duration(video_path: Path) -> float:
    """Runtime in seconds, or 0.0 when the file cannot be read."""
    try:
        reader = VideoReader(str(video_path), ctx=cpu(0))
        fps = float(reader.get_avg_fps())
        return len(reader) / fps if fps > 0 else 0.0
    except Exception:
        return 0.0


# --------------------------------------------------------------------------- #
# Weapon verdict
# --------------------------------------------------------------------------- #
# An object detector answers per frame, but a scan reports per video, and the
# two were joined by `len(detections) > 0` — one weak box in one of 24 sampled
# frames marked a whole clip as armed. That is exactly how a dog clip came back
# "Gun x1" at 43%, and how a ball does: a fine-tuned YOLOv8n fires weakly on
# round, dark, hand-held-looking objects, and the boxes it is least sure about
# are the ones most likely to be something harmless.
#
# Real footage of a real weapon does not look like that. The weapon is in shot
# for more than one sampled instant, so it lands in several sampled frames --
# or, if it genuinely only appears once, the detector is not hedging about it.
# Either is evidence; a lone uncertain box is not.
WEAPON_REPORT_CONFIDENCE = float(os.getenv("WEAPON_REPORT_CONFIDENCE", "0.45"))
WEAPON_DECISION_CONFIDENCE = float(os.getenv("WEAPON_DECISION_CONFIDENCE", "0.55"))
WEAPON_CORROBORATION_FRAMES = int(os.getenv("WEAPON_CORROBORATION_FRAMES", "2"))
WEAPON_LONE_CONFIDENCE = float(os.getenv("WEAPON_LONE_CONFIDENCE", "0.80"))


def weapon_verdict(
    detections: list[dict],
    *,
    min_confidence: float = WEAPON_DECISION_CONFIDENCE,
    min_frames: int = WEAPON_CORROBORATION_FRAMES,
    lone_confidence: float = WEAPON_LONE_CONFIDENCE,
) -> bool:
    """Whether per-frame boxes justify calling the whole video positive.

    Corroboration is counted per label and per *distinct sampled second*, not
    per box: a single frame that yields three overlapping boxes for the same
    knife is one sighting, and three boxes of three different classes in one
    frame is a detector guessing, not three weapons.

    Args:
        detections: Per-frame boxes (``label``/``score``/``second``).
        min_confidence: Score a box needs before it counts as evidence at all.
        min_frames: Distinct sampled seconds one label must appear in.
        lone_confidence: Score at which a single sighting stands on its own.

    Returns:
        True when at least one label is corroborated across ``min_frames``
        sampled seconds, or appears once at ``lone_confidence`` or better.
    """
    seconds_by_label: dict[str, set[float]] = {}
    for det in detections:
        try:
            score = float(det.get("score") or 0.0)
        except (TypeError, ValueError):
            continue
        if score < min_confidence:
            continue
        if score >= lone_confidence:
            return True
        label = str(det.get("label") or "object")
        try:
            second = round(float(det.get("second") or 0.0), 2)
        except (TypeError, ValueError):
            second = 0.0
        seconds_by_label.setdefault(label, set()).add(second)

    return any(len(seconds) >= min_frames for seconds in seconds_by_label.values())


def weapon_summary(detections: list[dict], label_counts: dict[str, int], positive: bool) -> str:
    """One honest sentence about what the object detector concluded.

    Boxes that did not add up to a verdict are still worth naming — an operator
    who can see "considered and rejected" trusts the clear result more than a
    silent one — but the sentence has to lead with the verdict, or a report that
    found nothing still reads as if it found a gun.
    """
    if positive:
        top = ", ".join(f"{name} x{n}" for name, n in list(label_counts.items())[:4])
        return f"Weapon detected: {top}." if top else "Weapon detected."
    if detections:
        return (
            "No weapon detected. Some low-confidence boxes were returned but "
            "none held up across enough frames to count as a sighting."
        )
    return "No weapons or objects of interest were found."


def _segment_from_run(
    label: str, run: list[dict], interval: float, duration: float, category: str
) -> dict:
    """Turn one run of same-label detections into a timeline segment."""
    start = float(run[0]["second"])
    # A detection marks the frame it was sampled from, not an instant: give it
    # the sampling interval it stands for, or a single hit would be zero-length.
    end = float(run[-1]["second"]) + interval
    if duration > 0:
        end = min(end, duration)
    best = max(float(det.get("score") or 0.0) for det in run)
    return {
        "label": label,
        "category": category,
        "description": (
            f"{label} detected in {len(run)} sampled frame(s) "
            f"({best * 100:.0f}% confidence)."
        ),
        "start_time": round(start, 2),
        "end_time": round(max(end, start + 0.1), 2),
        "confidence": round(best, 4),
    }


def detections_to_segments(
    detections: list[dict],
    duration_seconds: float,
    frames_scanned: int,
    category: str = "weapon",
) -> list[dict]:
    """Group per-frame object detections into timeline segments.

    Object detectors (YOLO, OWLv2) report one box per sampled frame, while the
    timeline — and ``LlmDetectionResponse`` — speak in spans. Same-label
    detections in consecutive sampled frames become one segment; a gap wider
    than the sampling interval starts a new one, so a knife that appears twice
    reads as two events rather than one span covering the quiet middle.
    """
    if not detections:
        return []

    interval = (
        duration_seconds / frames_scanned
        if frames_scanned > 0 and duration_seconds > 0
        else 1.0
    )
    gap = max(interval * 1.5, 0.5)

    by_label: dict[str, list[dict]] = {}
    for det in detections:
        by_label.setdefault(str(det.get("label") or "object"), []).append(det)

    segments: list[dict] = []
    for label, dets in by_label.items():
        dets = sorted(dets, key=lambda d: float(d.get("second") or 0.0))
        run = [dets[0]]
        for det in dets[1:]:
            if float(det["second"]) - float(run[-1]["second"]) <= gap:
                run.append(det)
            else:
                segments.append(
                    _segment_from_run(label, run, interval, duration_seconds, category)
                )
                run = [det]
        segments.append(
            _segment_from_run(label, run, interval, duration_seconds, category)
        )

    segments.sort(key=lambda seg: seg["start_time"])
    return segments


def focus_on_peak(
    segments: list[dict],
    duration_seconds: float,
    window_seconds: float,
    max_coverage: float = MAX_INCIDENT_COVERAGE,
    uniform_spread: float = UNIFORM_SPREAD,
) -> list[dict]:
    """Stop a segment from claiming the whole clip.

    Some checkpoints score every window of a short video above the threshold, so
    even after ``prune_to_peak`` the surviving run can span the entire runtime —
    and a timeline that is one unbroken band tells an operator nothing about
    where to look. Any span covering more than ``max_coverage`` of the video is
    pulled back to the window around its peak: the moment the model was most
    certain about. Spans that already sit inside part of the clip are left
    exactly as the detector reported them.

    A span is left alone when its windows all scored within ``uniform_spread``
    of each other. ``prune_to_peak`` has already decided that case: an even
    spread means the model called every second of the run the same incident, and
    a clip that really is one long event is the one case where the full span is
    the honest answer. Narrowing it would replace a measurement with a guess and
    hide the rest of the incident from the operator.

    Narrowed segments are flagged with ``narrowed`` so the caller can say so in
    the description rather than quietly presenting a guess as a measurement.
    """
    if duration_seconds <= 0 or max_coverage >= 1:
        return segments

    limit = max_coverage * duration_seconds
    span = min(max(window_seconds, 1.0), duration_seconds)

    for segment in segments:
        start = float(segment["start_time"])
        end = float(segment["end_time"])
        if end - start <= limit:
            continue

        spread = segment.get("confidence_spread")
        if spread is not None and float(spread) <= uniform_spread:
            continue

        peak = float(segment.get("peak_second") or (start + end) / 2)
        new_start = max(0.0, min(peak - span / 2, duration_seconds - span))
        segment["start_time"] = round(new_start, 2)
        segment["end_time"] = round(new_start + span, 2)
        segment["peak_second"] = round(peak, 2)
        segment["narrowed"] = True

    return segments


def build_windows(
    total_frames: int,
    fps: float,
    window_seconds: float,
    stride_seconds: float,
    min_frames: int = 1,
) -> list[tuple[int, int]]:
    """Sliding (start, end) frame ranges across the video.

    Every window holds at least ``min_frames``, because a video classifier is
    trained on a fixed clip length and raises on anything shorter ("The size of
    tensor a (392) must match the size of tensor b (1568)"), taking the whole
    scan down with it. Two things break that invariant, and both are handled:

    * ``window_seconds`` is derived from the clip's duration, which says nothing
      about its frame rate — a 1-second window on 10 fps footage is 10 frames,
      so *every* window comes out too short. The window is widened in frames.
    * Windows are clamped to the end of the video, so the last few starts
      produce ever-shorter stubs. Those are dropped and replaced by a single
      full-length window ending on the last frame, so the tail stays covered.

    Windows come out in chronological order, which callers rely on: the
    ``_merge_adjacent`` helpers only compare a hit against the span they are
    currently extending, so a window arriving out of order would open a second
    span over seconds the first one already covers.

    A video shorter than ``min_frames`` yields one short window — there is
    nothing better to classify, and the caller decides whether it survives.
    """
    window_frames = max(int(window_seconds * fps), min_frames, 1)
    stride_frames = max(int(stride_seconds * fps), 1)

    windows: list[tuple[int, int]] = []
    for start in range(0, total_frames, stride_frames):
        end = clamp(start + 1, start + window_frames, total_frames)
        # Every later start ends on the same last frame, so it is shorter still.
        if end - start < min_frames:
            break
        windows.append((start, end))

    # Whatever the stride left uncovered at the end gets one last full window.
    # Its start is necessarily past the previous one, so order is preserved.
    if windows and windows[-1][1] < total_frames:
        windows.append((max(0, total_frames - window_frames), total_frames))
    return windows or [(0, total_frames)]


def classify_window(
    frames: np.ndarray,
    id_to_label: dict[int, str],
    model: AutoModelForVideoClassification,
    processor: AutoImageProcessor,
    top_k: int,
) -> tuple[float, list[tuple[str, float]]]:
    inputs = processor(list(frames), return_tensors="pt")
    with torch.inference_mode():
        logits = model(**inputs).logits
        probabilities = torch.softmax(logits[0], dim=-1)

    top_values, top_indices = torch.topk(probabilities, k=min(top_k, probabilities.shape[-1]))
    top_predictions = [
        (id_to_label[int(index)], float(value))
        for value, index in zip(top_values.tolist(), top_indices.tolist())
    ]
    violence_score = compute_violence_score(
        [id_to_label[idx] for idx in range(len(id_to_label))], probabilities
    )
    return violence_score, top_predictions


def analyze_video_timeline(
    video_path: Path,
    model_id: str,
    num_frames: int,
    top_k: int,
    window_seconds: int,
    stride_seconds: int,
) -> dict[str, Any]:
    processor = AutoImageProcessor.from_pretrained(model_id, token=HF_TOKEN)
    model = AutoModelForVideoClassification.from_pretrained(model_id, token=HF_TOKEN)
    model.eval()
    reader = VideoReader(str(video_path), ctx=cpu(0))
    fps = float(reader.get_avg_fps())
    total_frames = len(reader)
    total_seconds = frame_to_seconds(total_frames, fps)
    id_to_label = model.config.id2label
    windows = build_windows(
        total_frames, fps, window_seconds, stride_seconds, min_frames=num_frames
    )

    segments = []
    for start_frame, end_frame in windows:
        frames = load_window_frames(reader, start_frame, end_frame, num_frames)
        if frames.size == 0:
            continue
        violence_score, top_predictions = classify_window(
            frames=frames,
            id_to_label=id_to_label,
            model=model,
            processor=processor,
            top_k=top_k,
        )
        segments.append(
            {
                "start_sec": frame_to_seconds(start_frame, fps),
                "end_sec": frame_to_seconds(end_frame, fps),
                "violence_score": violence_score,
                "top_predictions": top_predictions,
            }
        )

    if not segments:
        raise ValueError("No segments were generated from the input video.")

    scores = [seg["violence_score"] for seg in segments]
    average_score = float(sum(scores) / len(scores))
    max_score = max(scores)
    max_segment = max(segments, key=lambda seg: seg["violence_score"])

    high_risk_segments = [
        seg for seg in segments if seg["violence_score"] >= 0.25
    ]

    return {
        "average_score": average_score,
        "fps": fps,
        "high_risk_segments": high_risk_segments,
        "max_score": max_score,
        "max_segment": max_segment,
        "model_id": model_id,
        "segments": segments,
        "total_frames": total_frames,
        "total_seconds": total_seconds,
        "video_path": video_path,
    }


def detect_objects_in_video(
    video_path: Path,
    model_id: str,
    queries: Sequence[str],
    num_frames: int,
    score_threshold: float,
) -> dict[str, Any]:
    """Zero-shot open-vocabulary detection (OWLv2) across sampled frames.

    Scans each sampled frame for every text query and returns bounding boxes,
    confidence scores and timestamps for anything found above the threshold.
    """
    from PIL import Image
    from transformers import Owlv2ForObjectDetection, Owlv2Processor

    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor = Owlv2Processor.from_pretrained(model_id, token=HF_TOKEN)
    model = Owlv2ForObjectDetection.from_pretrained(model_id, token=HF_TOKEN)
    model.to(device)
    model.eval()

    reader = VideoReader(str(video_path), ctx=cpu(0))
    fps = float(reader.get_avg_fps())
    total_frames = len(reader)
    indices = sample_frame_indices(0, total_frames, num_frames)
    if indices.size == 0:
        raise ValueError("No frames could be sampled from the input video.")

    frames = reader.get_batch(indices.tolist()).asnumpy()
    text_labels = [list(queries)]

    detections: list[dict[str, Any]] = []
    for offset, frame_idx in enumerate(indices.tolist()):
        image = Image.fromarray(frames[offset])
        inputs = processor(
            text=text_labels, images=image, return_tensors="pt"
        ).to(device)
        with torch.inference_mode():
            outputs = model(**inputs)

        target_sizes = torch.tensor(
            [(image.height, image.width)], device=device
        )
        result = processor.post_process_grounded_object_detection(
            outputs=outputs,
            target_sizes=target_sizes,
            threshold=score_threshold,
            text_labels=text_labels,
        )[0]

        boxes = result["boxes"].tolist()
        scores = result["scores"].tolist()
        raw_labels = result.get("text_labels")
        if raw_labels is None:
            raw_labels = [queries[int(i)] for i in result["labels"].tolist()]

        for box, score, label in zip(boxes, scores, raw_labels):
            detections.append(
                {
                    "second": frame_to_seconds(frame_idx, fps),
                    "timestamp": format_seconds(frame_to_seconds(frame_idx, fps)),
                    "label": label,
                    "score": float(score),
                    "box_xyxy": [round(float(coord), 1) for coord in box],
                }
            )

    detections.sort(key=lambda det: det["score"], reverse=True)

    label_counts: dict[str, int] = {}
    for det in detections:
        label_counts[det["label"]] = label_counts.get(det["label"], 0) + 1

    return {
        "model_id": model_id,
        "queries": list(queries),
        "score_threshold": score_threshold,
        "frames_scanned": int(indices.size),
        "detection_count": len(detections),
        "label_counts": label_counts,
        "weapon_detected": len(detections) > 0,
        "detections": detections,
    }


def detect_objects_yolo(
    video_path: Path,
    num_frames: int,
    score_threshold: float,
) -> dict[str, Any]:
    """Detect weapons across sampled frames with a fine-tuned YOLOv8 model.

    Uses fixed trained classes (Gun/Knife/Grenade/Explosion) rather than
    free-text queries, giving higher precision on real firearms and blades.
    Returns the same shape as :func:`detect_objects_in_video`.
    """
    from ultralytics import YOLO

    if YOLO_MODEL_PATH:
        weights_path = YOLO_MODEL_PATH
        model_id = YOLO_MODEL_PATH
    else:
        from huggingface_hub import hf_hub_download

        weights_path = hf_hub_download(
            repo_id=YOLO_MODEL_REPO, filename=YOLO_MODEL_FILE, token=HF_TOKEN
        )
        model_id = f"{YOLO_MODEL_REPO}/{YOLO_MODEL_FILE}"

    model = YOLO(weights_path)
    class_names: dict[int, str] = dict(model.names)

    reader = VideoReader(str(video_path), ctx=cpu(0))
    fps = float(reader.get_avg_fps())
    total_frames = len(reader)
    indices = sample_frame_indices(0, total_frames, num_frames)
    if indices.size == 0:
        raise ValueError("No frames could be sampled from the input video.")

    frames = reader.get_batch(indices.tolist()).asnumpy()

    detections: list[dict[str, Any]] = []
    for offset, frame_idx in enumerate(indices.tolist()):
        results = model.predict(
            frames[offset], conf=score_threshold, verbose=False
        )
        second = frame_to_seconds(frame_idx, fps)
        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                class_id = int(box.cls[0])
                detections.append(
                    {
                        "second": second,
                        "timestamp": format_seconds(second),
                        "label": class_names.get(class_id, str(class_id)),
                        "score": float(box.conf[0]),
                        "box_xyxy": [
                            round(float(coord), 1) for coord in box.xyxy[0].tolist()
                        ],
                    }
                )

    detections.sort(key=lambda det: det["score"], reverse=True)

    label_counts: dict[str, int] = {}
    for det in detections:
        label_counts[det["label"]] = label_counts.get(det["label"], 0) + 1

    return {
        "model_id": model_id,
        "queries": [class_names[i] for i in sorted(class_names)],
        "score_threshold": score_threshold,
        "frames_scanned": int(indices.size),
        "detection_count": len(detections),
        "label_counts": label_counts,
        "weapon_detected": len(detections) > 0,
        "detections": detections,
    }


def generate_qwen_video_report(video_path: Path) -> str:
    try:
        from qwen_vl_utils import process_vision_info
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "qwen_vl_utils is not installed. Install with: pip install qwen-vl-utils"
        ) from exc

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        QWEN_MODEL_ID,
        torch_dtype="auto",
        device_map=device,
    )
    processor = AutoProcessor.from_pretrained(QWEN_MODEL_ID)
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "video",
                    "video": f"file://{video_path.resolve().as_posix()}",
                },
                {
                    "type": "text",
                    "text": (
                        "Analyze this full video and provide: "
                        "1) timeline summary of key events, "
                        "2) any potential violence or threat indicators, "
                        "3) confidence notes and ambiguities."
                    ),
                },
            ],
        }
    ]
    text = processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    image_inputs, video_inputs = process_vision_info(messages)
    inputs = processor(
        text=[text],
        images=image_inputs,
        videos=video_inputs,
        padding=True,
        return_tensors="pt",
    )
    inputs = inputs.to(device)
    with torch.inference_mode():
        generated_ids = model.generate(**inputs, max_new_tokens=320)
    generated_ids_trimmed = [
        out_ids[len(in_ids) :] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
    ]
    output_text = processor.batch_decode(
        generated_ids_trimmed,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False,
    )
    return output_text[0] if output_text else ""


def print_detailed_report(results: dict[str, Any], top_k: int) -> None:
    print(f"model: {results['model_id']}")
    print(f"video: {results['video_path']}")
    print(f"duration: {results['total_seconds']:.2f}s")
    print(f"frames: {results['total_frames']} (fps={results['fps']:.2f})")
    print(f"average_violence_score: {results['average_score']:.4f}")
    print(f"max_violence_score: {results['max_score']:.4f}")

    max_seg = results["max_segment"]
    print(
        "highest_risk_window:"
        f" {format_seconds(max_seg['start_sec'])} - {format_seconds(max_seg['end_sec'])}"
        f" (score={max_seg['violence_score']:.4f})"
    )

    print("\nsegment_details:")
    for idx, seg in enumerate(results["segments"], start=1):
        start = format_seconds(seg["start_sec"])
        end = format_seconds(seg["end_sec"])
        print(
            f"- segment_{idx}: {start} - {end} | violence_score={seg['violence_score']:.4f}"
        )
        for label, score in seg["top_predictions"][:top_k]:
            print(f"    * {label}: {score:.4f}")

    print("\nhigh_risk_segments(score>=0.25):")
    high_risk_segments = results["high_risk_segments"]
    if not high_risk_segments:
        print("- none")
    else:
        for seg in high_risk_segments:
            start = format_seconds(seg["start_sec"])
            end = format_seconds(seg["end_sec"])
            print(f"- {start} - {end} | score={seg['violence_score']:.4f}")


def print_object_report(detection: dict[str, Any], top_k: int) -> None:
    print("\nobject_detection:")
    print(f"model: {detection['model_id']}")
    print(f"frames_scanned: {detection['frames_scanned']}")
    print(f"weapon_or_object_detected: {detection['weapon_detected']}")
    print(f"total_detections: {detection['detection_count']}")

    if detection["label_counts"]:
        print("counts_by_label:")
        for label, count in sorted(
            detection["label_counts"].items(), key=lambda kv: kv[1], reverse=True
        ):
            print(f"    * {label}: {count}")

    print(f"\ntop_detections (up to {top_k}):")
    if not detection["detections"]:
        print("- none")
        return
    for det in detection["detections"][:top_k]:
        print(
            f"- {det['timestamp']} | {det['label']}"
            f" | score={det['score']:.4f} | box={det['box_xyxy']}"
        )


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    return value


def save_json_report(
    output_path: Path,
    results: dict[str, Any],
    llm_report: str | None,
    object_detection: dict[str, Any] | None,
) -> None:
    payload = to_jsonable(
        {
            "results": results,
            "object_detection": object_detection,
            "llm_vision_report": llm_report,
        }
    )
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run VideoMAE-based video classification and violence score estimation."
    )
    parser.add_argument(
        "--video",
        type=Path,
        required=True,
        help="Path to the input video file.",
    )
    parser.add_argument(
        "--model-id",
        default=VIDEO_MODEL_ID,
        help="Hugging Face model id. Default is VIDEO_MODEL_ID env or VideoMAE checkpoint.",
    )
    parser.add_argument(
        "--num-frames",
        type=int,
        default=16,
        help="Number of frames to sample from the video.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of top classes to print.",
    )
    parser.add_argument(
        "--window-seconds",
        type=int,
        default=4,
        help="Window size in seconds for full-video timeline analysis.",
    )
    parser.add_argument(
        "--stride-seconds",
        type=int,
        default=2,
        help="Stride in seconds between timeline windows.",
    )
    parser.add_argument(
        "--detect-objects",
        action="store_true",
        help="Run weapon/object detection on sampled frames.",
    )
    parser.add_argument(
        "--detector",
        choices=("owlv2", "yolo"),
        default="owlv2",
        help=(
            "Detector for --detect-objects: 'owlv2' (zero-shot, free-text "
            "queries) or 'yolo' (fine-tuned weapon model, fixed classes)."
        ),
    )
    parser.add_argument(
        "--object-model-id",
        default=OBJECT_MODEL_ID,
        help="Hugging Face OWLv2 model id for object detection.",
    )
    parser.add_argument(
        "--object-queries",
        nargs="+",
        default=None,
        help=(
            "Text queries to detect (e.g. gun knife rifle). "
            "Defaults to a built-in weapon/violence query list."
        ),
    )
    parser.add_argument(
        "--object-frames",
        type=int,
        default=24,
        help="Number of frames to sample for object detection.",
    )
    parser.add_argument(
        "--object-threshold",
        type=float,
        default=0.2,
        help="Minimum confidence score to keep a detection.",
    )
    parser.add_argument(
        "--with-llm-report",
        action="store_true",
        help="Add Qwen vision narrative report for the full video.",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Optional path to save a full JSON report.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.video.exists():
        raise FileNotFoundError(f"Video file not found: {args.video}")

    results = analyze_video_timeline(
        video_path=args.video,
        model_id=args.model_id,
        num_frames=args.num_frames,
        top_k=args.top_k,
        window_seconds=args.window_seconds,
        stride_seconds=args.stride_seconds,
    )
    print_detailed_report(results, top_k=args.top_k)

    object_detection: dict[str, Any] | None = None
    if args.detect_objects:
        try:
            if args.detector == "yolo":
                object_detection = detect_objects_yolo(
                    video_path=args.video,
                    num_frames=args.object_frames,
                    score_threshold=args.object_threshold,
                )
            else:
                queries = (
                    tuple(args.object_queries)
                    if args.object_queries
                    else DEFAULT_OBJECT_QUERIES
                )
                object_detection = detect_objects_in_video(
                    video_path=args.video,
                    model_id=args.object_model_id,
                    queries=queries,
                    num_frames=args.object_frames,
                    score_threshold=args.object_threshold,
                )
            print_object_report(object_detection, top_k=args.top_k)
        except Exception as exc:
            print(f"\nunable_to_run_object_detection: {exc}")

    llm_report: str | None = None
    if args.with_llm_report:
        print("\nllm_vision_report:")
        try:
            llm_report = generate_qwen_video_report(args.video)
            print(llm_report)
        except Exception as exc:
            print(f"unable_to_generate_llm_report: {exc}")

    if args.json_out is not None:
        save_json_report(args.json_out, results, llm_report, object_detection)
        print(f"\njson_report_saved: {args.json_out}")


if __name__ == "__main__":
    main()
