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


def build_windows(
    total_frames: int, fps: float, window_seconds: int, stride_seconds: int
) -> list[tuple[int, int]]:
    window_frames = max(int(window_seconds * fps), 1)
    stride_frames = max(int(stride_seconds * fps), 1)
    starts = range(0, total_frames, stride_frames)
    return [
        (start, clamp(start + 1, start + window_frames, total_frames))
        for start in starts
    ]


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
    windows = build_windows(total_frames, fps, window_seconds, stride_seconds)

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
