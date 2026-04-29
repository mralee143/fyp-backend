import argparse
import os
from pathlib import Path
from typing import Sequence

import numpy as np
import torch
from decord import VideoReader, cpu
from transformers import AutoImageProcessor, AutoModelForVideoClassification

HF_TOKEN = os.getenv("HF_TOKEN")
VIDEO_MODEL_ID = os.getenv(
    "VIDEO_MODEL_ID", "MCG-NJU/videomae-base-finetuned-kinetics"
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


def sample_frame_indices(total_frames: int, num_frames: int) -> np.ndarray:
    if total_frames <= 0:
        return np.array([], dtype=np.int64)
    if total_frames <= num_frames:
        return np.arange(total_frames, dtype=np.int64)
    return np.linspace(0, total_frames - 1, num_frames, dtype=np.int64)


def load_frames(video_path: Path, num_frames: int) -> np.ndarray:
    reader = VideoReader(str(video_path), ctx=cpu(0))
    indices = sample_frame_indices(len(reader), num_frames)
    if indices.size == 0:
        raise ValueError("No frames found in the provided video.")
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


def classify_video(
    video_path: Path, model_id: str, num_frames: int, top_k: int
) -> tuple[float, list[tuple[str, float]]]:
    processor = AutoImageProcessor.from_pretrained(model_id, token=HF_TOKEN)
    model = AutoModelForVideoClassification.from_pretrained(model_id, token=HF_TOKEN)
    model.eval()

    frames = load_frames(video_path, num_frames)
    inputs = processor(list(frames), return_tensors="pt")

    with torch.inference_mode():
        logits = model(**inputs).logits
        probabilities = torch.softmax(logits[0], dim=-1)

    id_to_label = model.config.id2label
    top_values, top_indices = torch.topk(probabilities, k=min(top_k, probabilities.shape[-1]))
    top_predictions = [
        (id_to_label[int(index)], float(value))
        for value, index in zip(top_values.tolist(), top_indices.tolist())
    ]
    violence_score = compute_violence_score(
        [id_to_label[idx] for idx in range(len(id_to_label))], probabilities
    )
    return violence_score, top_predictions


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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.video.exists():
        raise FileNotFoundError(f"Video file not found: {args.video}")

    violence_score, predictions = classify_video(
        video_path=args.video,
        model_id=args.model_id,
        num_frames=args.num_frames,
        top_k=args.top_k,
    )

    print(f"model: {args.model_id}")
    print(f"video: {args.video}")
    print(f"violence_score: {violence_score:.4f}")
    print("top_predictions:")
    for label, score in predictions:
        print(f"  - {label}: {score:.4f}")


if __name__ == "__main__":
    main()
