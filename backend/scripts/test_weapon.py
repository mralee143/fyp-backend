"""
Quick weapon-detection test on a single video.

Runs only the object/weapon detector (skips the slow action-classification
timeline in vid_img.py), so it's fast for trying out clips.

Usage:
    python test_weapon.py <video> [yolo|owlv2] [threshold]

Examples:
    python test_weapon.py weapon.mp4                # yolo, threshold 0.35
    python test_weapon.py weapon.mp4 yolo 0.5       # stricter yolo
    python test_weapon.py weapon.mp4 owlv2 0.2      # zero-shot, default queries
"""

import sys
from pathlib import Path

from ml.vid_img import (
    DEFAULT_OBJECT_QUERIES,
    OBJECT_MODEL_ID,
    detect_objects_in_video,
    detect_objects_yolo,
    print_object_report,
)

NUM_FRAMES = 16


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python test_weapon.py <video> [yolo|owlv2] [threshold]")
        raise SystemExit(1)

    video = Path(sys.argv[1])
    detector = sys.argv[2] if len(sys.argv) > 2 else "yolo"
    threshold = float(sys.argv[3]) if len(sys.argv) > 3 else 0.35

    if not video.exists():
        print(f"file not found: {video}")
        raise SystemExit(1)

    print(f"detecting on {video} (detector={detector}, threshold={threshold})...")

    if detector == "yolo":
        result = detect_objects_yolo(
            video_path=video,
            num_frames=NUM_FRAMES,
            score_threshold=threshold,
        )
    else:
        result = detect_objects_in_video(
            video_path=video,
            model_id=OBJECT_MODEL_ID,
            queries=DEFAULT_OBJECT_QUERIES,
            num_frames=NUM_FRAMES,
            score_threshold=threshold,
        )

    print_object_report(result, top_k=10)


if __name__ == "__main__":
    main()
