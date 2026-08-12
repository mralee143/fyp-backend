# Models — complete list, what each does, and memory

Every size below was measured inside the image, not estimated:

```powershell
docker run --rm --network none --entrypoint sh aleehaider045/sentinelai-backend:latest `
  -c 'du -sh /opt/models/hub/* /opt/ollama'
```

---

## 1. Runs locally, baked into the image

Nothing here touches the network. Verified by loading every one of them in a
container started with `--network none`.

| # | Model | Backend | What it does | Params | On disk | RAM when loaded |
|---|---|---|---|---|---|---|
| 1 | `OPear/videomae-large-finetuned-UCF-Crime` | `model=action` | Recognises *actions* over time — fighting, assault, abuse, robbery, shooting, explosion (UCF-Crime classes) | 303.9 M | 1.2 GB | ~1.2 GB |
| 2 | `google/owlv2-base-patch16-ensemble` | `model=owlv2` | Finds **any** object from a free-text query (zero-shot) | 155.0 M | 593 MB | ~0.6 GB |
| 3 | `cliffer1/videomae-base-…-violence-nonviolence` | `model=violence` | Binary violence / non-violence — strongest on fights and assault | 86.2 M | 330 MB | ~0.35 GB |
| 4 | `Subh775/Threat-Detection-YOLOv8n` | `model=yolo` | Fixed weapon classes: `Gun`, `knife`, `grenade`, `explosion` | 3.01 M | 6.0 MB | ~15 MB |
| 5 | `yolov8n.pt` (bundled) | annotation | Draws boxes on extracted incident clips | 3.2 M | 6.3 MB | ~15 MB |
| 6 | `qwen2.5:3b` via **Ollama** | `/chat/*` | Agentic chat + tool calling, 32k context — **Q4_K_M quantized** | 3.1 B | 1.8 GB | ~2.5 GB |
| 7 | `Qwen/Qwen2.5-VL-7B-Instruct` | `model=qwen` | Local vision-language video analysis — all five incident categories, keyless | 7.6 B | 16.6 GB | ~17 GB |

Qwen2.5 **chat** (#6) and Qwen2.5-**VL** (#7) are different models despite the
similar name: #6 is a 4-bit text model driving the chat agent, #7 is a
full-precision vision model that watches video.

`#7 is opt-in` — `INCLUDE_QWEN_VL=true` in `.env.production`. Set it false and
rebuild to drop 16.6 GB.

---

## 2. Hosted — not in the image, by design

| Model | Backend | Why it is not baked |
|---|---|---|
| Gemini (`gemini-flash-latest`) | `model=llm`, `auto`, scan chat | It is an API — there is nothing to bake. Needs `GEMINI_API_KEY`. |

**Gemini is the most important model in the system.** In the `auto` cascade it
runs first and its answer is final *either way*; the local models only run when
it could not answer at all. Taking the first *positive* across all backends
compounded every model's false-positive rate — see `docs/` history and the
comment block in `services/detection_runner.py::_run_auto`.

---

## 3. Which models can detect what

| Capability | Models that can do it |
|---|---|
| Weapons as objects | YOLOv8n weapon, OWLv2 |
| Arbitrary objects | OWLv2 |
| Actions over time (fighting, robbery…) | VideoMAE action, VideoMAE violence |
| **Harassment** (contextual, needs intent) | **Gemini, Qwen2.5-VL** |
| Theft / accident / free-text reasoning | Gemini, Qwen2.5-VL |
| Conversation about a scan | Qwen2.5 3B chat, Gemini |

Harassment needs a vision-*language* model: it is about intent, consent and
persistence, none of which is an object or a pose. The classifiers in rows 1-3
of section 1 structurally cannot do it — UCF-Crime has no `harassment` label.

---

## 4. Gemini vs Qwen2.5-VL — same shape, different quality

Both return the identical JSON (`violence_detected`, `summary`, `segments[]`)
over the same five categories, which is why `_shape()` treats them
interchangeably. They are not equivalent in practice:

| | Gemini | Qwen2.5-VL 7B |
|---|---|---|
| Location | Hosted API | In the image |
| Video it sees | The whole clip, uploaded | **8 frames @ 512×384** |
| Speed | Seconds | Minutes on CPU |
| Requires | API key + quota | Nothing |
| Position in `auto` | **First; verdict is final** | **Last**; only if Gemini failed *and* all four classifiers found nothing |

The frame budget is the real gap: `nframes=8` in `ml/qwen_infer.py` means a
short incident between sampled frames is invisible to it. Raise it for better
recall at a roughly linear cost in inference time.

---

## 5. Memory planning

Models load **lazily on first use and stay cached** for the life of the process,
so peak depends on which backends actually get exercised.

| Always resident | |
|---|---|
| Python + torch baseline (measured) | ~220 MB |
| Postgres + Redis + MinIO | ~500 MB |

| Scenario | Peak RAM |
|---|---|
| Idle, nothing loaded | ~700 MB |
| Typical — one detector + chat | ~3.5 GB |
| All vision classifiers + chat, **no** Qwen2.5-VL | ~5 GB |
| Same, **plus** Qwen2.5-VL 7B | **~22 GB** |

**Sizing:**
- Without Qwen2.5-VL (`INCLUDE_QWEN_VL=false`): **8 GB minimum, 16 GB comfortable**
- With Qwen2.5-VL 7B: **32 GB** — the model alone is ~17 GB in bf16
- With Qwen2.5-VL **3B** instead: ~8 GB for the model, so 16 GB total is workable

> Measuring this yourself: `ru_maxrss` is a *peak* counter, so a model loaded
> after a larger one appears free. Safetensors are also memory-mapped, so RSS
> stays far below true weight size until inference touches the pages. Read
> `VmRSS` from `/proc/self/status` in a **fresh process per model**, or use
> params × dtype-bytes.

---

## 6. CPU vs GPU

The image ships **CPU** torch wheels and Ollama's CPU runners — the CUDA, MLX
and Vulkan runners are pruned in the `ollama-src` stage, cutting
`/usr/lib/ollama` from 3.1 GB to 29 MB. Everything runs anywhere, just slower.

For an NVIDIA GPU:
1. Install the NVIDIA Container Toolkit
2. Build with `TORCH_INDEX_URL=https://download.pytorch.org/whl/cu126`
3. Delete the matching `cuda_*` line from the prune in `backend/Dockerfile`
4. Uncomment `deploy.resources` on `api`/`worker` in `docker-compose.yml`
5. Add `bitsandbytes` to the venv — `ml/qwen_infer.py` uses 4-bit quantization
   on the CUDA path only, which drops Qwen2.5-VL 7B from ~17 GB to ~6 GB VRAM

---

## 7. Swapping a checkpoint

Each is an env var, so a different checkpoint needs no code change — but it
would then be **downloaded at run time**, defeating the point of baking. Pass
the matching build arg so the weights land in the image instead:

| Env var / build arg | Default |
|---|---|
| `ACTION_MODEL_ID` | `OPear/videomae-large-finetuned-UCF-Crime` |
| `VIOLENCE_MODEL_ID` | `cliffer1/videomae-base-finetuned-kinetics-violence-nonviolence-tuned` |
| `OBJECT_MODEL_ID` | `google/owlv2-base-patch16-ensemble` |
| `YOLO_MODEL_REPO` / `YOLO_MODEL_FILE` | `Subh775/Threat-Detection-YOLOv8n` / `weights/best.pt` |
| `QWEN_CHAT_MODEL` | `qwen2.5:3b` |
| `INCLUDE_QWEN_VL` / `QWEN_VL_MODEL_ID` | `false` / `Qwen/Qwen2.5-VL-7B-Instruct` |
| `GEMINI_MODEL` / `CHAT_MODEL` | `gemini-flash-latest` |

```powershell
docker compose build --build-arg QWEN_VL_MODEL_ID=Qwen/Qwen2.5-VL-3B-Instruct api
```
