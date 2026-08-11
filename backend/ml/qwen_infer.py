"""
Qwen2.5-VL local video violence analysis.

Runs INSIDE `qwen_env` (separate from the backend env). Reads a video path from
argv, asks Qwen2.5-VL to detect violence/theft/harassment, and prints one line:

    __QWEN_RESULT__{...json...}

The backend (services/qwen_detection.py) spawns this and parses that line.
"""

import json
import os
import re
import sys

import torch
from transformers import (
    Qwen2_5_VLForConditionalGeneration,
    AutoProcessor,
    BitsAndBytesConfig,
)
from qwen_vl_utils import process_vision_info

MODEL_ID = "Qwen/Qwen2.5-VL-7B-Instruct"
SENTINEL = "__QWEN_RESULT__"

PROMPT_TMPL = (
    "You are a video safety analyst. The clip is about {dur:.0f} seconds long. "
    "Watch it and detect any dangerous or criminal incident:\n"
    "- VIOLENCE: fighting, punching, kicking, assault, beating, shooting, "
    "explosion, killing. A visible WEAPON (gun, rifle, knife, grenade) always "
    "counts as violence, even if only held.\n"
    "- THEFT/ROBBERY: snatching, mugging, burglary, shoplifting, stealing.\n"
    "- HARASSMENT: unwanted contact, stalking, intimidation.\n"
    "- ACCIDENT: road/car accidents, crashes, collisions, someone falling or "
    "being hit.\n"
    "- OTHER: fire, arson, vandalism, or any other dangerous emergency.\n\n"
    "Reply with ONLY a JSON object (no markdown, no extra text) shaped exactly like:\n"
    '{{"violence_detected": true, "summary": "one sentence", "segments": '
    '[{{"label": "Fighting", "category": "violence", "description": "what happens", '
    '"start_time": 0, "end_time": {dur:.0f}, "confidence": 0.9}}]}}\n\n'
    "Rules: category is one of violence, theft, harassment, accident, other. "
    "Set violence_detected=true if ANY incident above is found (including an "
    "accident). Timestamps are seconds in 0..{dur:.0f}. confidence is 0..1. If "
    "nothing dangerous happens, set violence_detected=false and segments=[]."
)


def _duration(path: str) -> float:
    try:
        from decord import VideoReader

        vr = VideoReader(path)
        return float(len(vr)) / float(vr.get_avg_fps() or 1.0)
    except Exception:
        return 0.0


def _parse_json(text: str) -> dict:
    text = text.strip().strip("`")
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {"violence_detected": False, "summary": text[:200], "segments": []}


def main() -> None:
    # Absolute, forward-slash path — decord/qwen-vl-utils choke on relative
    # paths and on the file:// scheme on Windows.
    path = os.path.abspath(sys.argv[1]).replace("\\", "/")
    dur = _duration(path) or 10.0
    device = "cuda" if torch.cuda.is_available() else "cpu"

    if device == "cuda":
        # 4-bit quantization so the 7B model fits in ~8 GB VRAM and runs fast.
        bnb = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
        )
        model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            MODEL_ID, quantization_config=bnb, device_map="auto"
        )
    else:
        model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            MODEL_ID, torch_dtype="auto", device_map=device
        )
    processor = AutoProcessor.from_pretrained(MODEL_ID)

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "video",
                    "video": path,
                    # Fixed small frame count keeps CPU inference fast; each
                    # extra frame adds a lot of vision tokens.
                    "nframes": 8,
                    "max_pixels": 512 * 384,
                },
                {"type": "text", "text": PROMPT_TMPL.format(dur=dur)},
            ],
        }
    ]

    text = processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    vision = process_vision_info(messages)
    if len(vision) == 3:
        image_inputs, video_inputs, video_kwargs = vision
    else:
        image_inputs, video_inputs = vision
        video_kwargs = {}

    inputs = processor(
        text=[text],
        images=image_inputs,
        videos=video_inputs,
        padding=True,
        return_tensors="pt",
        **video_kwargs,
    ).to(device)

    with torch.no_grad():
        generated = model.generate(**inputs, max_new_tokens=256, do_sample=False)
    trimmed = [o[len(i):] for i, o in zip(inputs.input_ids, generated)]
    out = processor.batch_decode(
        trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
    )[0]

    print(f"RAW_QWEN_OUTPUT: {out[:800]!r}", file=sys.stderr)
    result = _parse_json(out)
    segments = result.get("segments") or []
    payload = {
        "model_id": "Qwen2.5-VL-7B (local)",
        "violence_detected": bool(result.get("violence_detected", bool(segments))),
        "summary": str(result.get("summary", "")),
        "segments": segments,
    }
    print(SENTINEL + json.dumps(payload))


if __name__ == "__main__":
    main()
