"""Pre-download every model the backend can load, at image build time.

Without this the first analysis stalls for minutes while ~2.5 GB comes down
from the Hub, and a container with no network cannot analyse anything at all.
Each model is fetched exactly the way the runtime loads it, so the cache entries
land under the keys `from_pretrained` will look for.

Fetched into HF_HOME (set to /opt/models in the Dockerfile):

    OBJECT_MODEL_ID     OWLv2        open-vocabulary object detection
    ACTION_MODEL_ID     VideoMAE     UCF-Crime action recognition
    VIOLENCE_MODEL_ID   VideoMAE     binary violence classifier
    YOLO_MODEL_REPO     YOLOv8n      fine-tuned weapon detector

Any failure fails the build. A half-stocked image is worse than no image: it
looks complete and then falls over on the first upload of that kind.
"""

import os
import sys

# Must match the defaults in ml/vid_img.py / services/*_detection.py, and honour
# the same env vars so a build can pin different checkpoints.
OBJECT_MODEL_ID = os.getenv("OBJECT_MODEL_ID", "google/owlv2-base-patch16-ensemble")
ACTION_MODEL_ID = os.getenv("ACTION_MODEL_ID", "OPear/videomae-large-finetuned-UCF-Crime")
VIOLENCE_MODEL_ID = os.getenv(
    "VIOLENCE_MODEL_ID",
    "cliffer1/videomae-base-finetuned-kinetics-violence-nonviolence-tuned",
)
YOLO_MODEL_REPO = os.getenv("YOLO_MODEL_REPO", "Subh775/Threat-Detection-YOLOv8n")
YOLO_MODEL_FILE = os.getenv("YOLO_MODEL_FILE", "weights/best.pt")


def video_classifier(model_id: str) -> None:
    from transformers import AutoImageProcessor, AutoModelForVideoClassification

    AutoImageProcessor.from_pretrained(model_id)
    AutoModelForVideoClassification.from_pretrained(model_id)


def owlv2(model_id: str) -> None:
    from transformers import Owlv2ForObjectDetection, Owlv2Processor

    Owlv2Processor.from_pretrained(model_id)
    Owlv2ForObjectDetection.from_pretrained(model_id)


def yolo_weights(repo: str, filename: str) -> None:
    from huggingface_hub import hf_hub_download

    path = hf_hub_download(repo_id=repo, filename=filename)
    print(f"       -> {path}", flush=True)


TARGETS = [
    ("OWLv2 object detection", owlv2, (OBJECT_MODEL_ID,)),
    ("VideoMAE action (UCF-Crime)", video_classifier, (ACTION_MODEL_ID,)),
    ("VideoMAE violence", video_classifier, (VIOLENCE_MODEL_ID,)),
    ("YOLOv8n weapon weights", yolo_weights, (YOLO_MODEL_REPO, YOLO_MODEL_FILE)),
]


def main() -> int:
    print(f"Caching models into HF_HOME={os.getenv('HF_HOME')}", flush=True)
    for label, fetch, args in TARGETS:
        print(f"[fetch] {label}: {args[0]}", flush=True)
        try:
            fetch(*args)
        except Exception as exc:
            print(f"[FAIL]  {label}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(f"[ok]    {label}", flush=True)

    # Ultralytics writes a settings file on first run and will try to reach the
    # network for it if the directory is missing. YOLO_CONFIG_DIR points here.
    from ultralytics import YOLO

    YOLO("yolov8n.pt")
    print("[ok]    ultralytics yolov8n (generic boxes for annotation)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
