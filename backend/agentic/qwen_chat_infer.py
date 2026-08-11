"""
Offline Qwen chat generation (fallback path for the agent chatbot).

Runs INSIDE `qwen_env` (separate from the backend env). Reads a JSON payload
from stdin:

    {"messages": [{"role": "user", "content": "..."}], "max_tokens": 1024}

and prints one line:

    __QWEN_CHAT__{"content": "..."}

The backend (agentic/qwen_chat.py) spawns this only when the configured
OpenAI-compatible Qwen endpoint is unreachable. It reuses the Qwen2.5-VL
weights already cached by qwen_infer.py, so nothing extra is downloaded — but
loading a 7B model per turn is slow. Prefer running Ollama.

Placement is decided from *free* VRAM at spawn time, not total VRAM: on a
desktop GPU the browser and compositor can be holding several GB, so a plain
`device_map="auto"` lets accelerate spill layers to CPU, which bitsandbytes
refuses outright ("set llm_int8_enable_fp32_cpu_offload=True..."). Splitting is
not an option either — 4-bit params can't be moved back and forth by accelerate
hooks ("Cannot copy out of meta tensor") — so it is all-GPU or all-CPU, and we
drop to CPU whenever the quantized weights don't fit in what's actually free.
"""

import json
import os
import sys

import torch
from transformers import (
    AutoConfig,
    AutoModelForCausalLM,
    AutoProcessor,
    AutoTokenizer,
    BitsAndBytesConfig,
    Qwen2_5_VLForConditionalGeneration,
)

# Reuse the VL weights qwen_infer.py already downloaded; a text-only Qwen
# (e.g. Qwen/Qwen2.5-3B-Instruct) works too via QWEN_CHAT_LOCAL_MODEL and is a
# much better fit for an 8 GB card.
MODEL_ID = os.getenv("QWEN_CHAT_LOCAL_MODEL", "Qwen/Qwen2.5-VL-7B-Instruct")
SENTINEL = "__QWEN_CHAT__"

# Left free on the GPU for activations and the KV cache, on top of the weights.
VRAM_HEADROOM_GIB = float(os.getenv("QWEN_CHAT_VRAM_HEADROOM_GIB", "1.0"))
# Below this there is no point trying the GPU at all.
MIN_GPU_GIB = float(os.getenv("QWEN_CHAT_MIN_GPU_GIB", "2.0"))


def _free_vram_gib() -> float:
    """VRAM available to the weights right now, in GiB (0 means CPU-only)."""
    if not torch.cuda.is_available():
        return 0.0
    free, _total = torch.cuda.mem_get_info()
    return max(0.0, free / 2**30 - VRAM_HEADROOM_GIB)


def _load(model_id: str):
    """Load the model and its tokenizer/processor, on GPU if it fits, else CPU."""
    config = AutoConfig.from_pretrained(model_id)
    is_vl = "vl" in getattr(config, "model_type", "")
    model_cls = Qwen2_5_VLForConditionalGeneration if is_vl else AutoModelForCausalLM
    # AutoProcessor only exists for the multimodal checkpoints; text-only Qwen
    # needs the plain tokenizer. Both expose apply_chat_template/batch_decode.
    tokenizer = (AutoProcessor if is_vl else AutoTokenizer).from_pretrained(model_id)

    free = _free_vram_gib()
    if free >= MIN_GPU_GIB:
        print(f"qwen_chat: trying 4-bit on GPU ({free:.1f} GiB free)", file=sys.stderr)
        try:
            model = model_cls.from_pretrained(
                model_id,
                quantization_config=BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.float16,
                ),
                # Pinned to device 0 on purpose: "auto" would silently spill to
                # CPU, and a partly-offloaded 4-bit model can't run.
                device_map={"": 0},
            )
            return model, tokenizer
        except (torch.OutOfMemoryError, RuntimeError, ValueError) as e:
            print(f"qwen_chat: GPU load failed ({e}); using CPU", file=sys.stderr)
            torch.cuda.empty_cache()
    else:
        print(f"qwen_chat: only {free:.1f} GiB VRAM free; using CPU", file=sys.stderr)

    # bfloat16 rather than "auto"/fp32 — halves the RAM the weights need.
    model = model_cls.from_pretrained(model_id, dtype=torch.bfloat16, device_map="cpu")
    return model, tokenizer


def main() -> None:
    payload = json.loads(sys.stdin.read() or "{}")
    messages = payload.get("messages") or []
    max_tokens = int(payload.get("max_tokens", 1024))

    model, tokenizer = _load(MODEL_ID)

    # Text-only conversation — no vision inputs to process.
    text = tokenizer.apply_chat_template(
        [{"role": m.get("role", "user"), "content": str(m.get("content", ""))} for m in messages],
        tokenize=False,
        add_generation_prompt=True,
    )
    # Not `.to("cuda")`: on an offloaded split the inputs belong on whichever
    # device holds the embedding layer, which is what `model.device` reports.
    inputs = tokenizer(text=[text], padding=True, return_tensors="pt").to(model.device)

    with torch.no_grad():
        generated = model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False)
    trimmed = [o[len(i):] for i, o in zip(inputs.input_ids, generated)]
    out = tokenizer.batch_decode(
        trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
    )[0]

    print(SENTINEL + json.dumps({"content": out.strip()}))


if __name__ == "__main__":
    main()
