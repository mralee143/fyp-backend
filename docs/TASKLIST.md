# 🎯 Task List — AI Violence, Crime & Weapon Video Detection

Full-stack Final Year Project: **FastAPI backend** (weapon + violence/crime detection) and **Next.js frontend** (login → dashboard → upload video → view threat detail).

**Target detections:** Fighting, Snatching/Robbery, Killing/Shooting, Harassment/Assault, Bomb/Explosion, Weapons (gun/knife/grenade).

---

## Already done (backend)
- JWT auth — `/auth/signup`, `/auth/login`
- Users + image storage (MinIO)
- Video detection `/detection/video` with two backends: **OWLv2** (zero-shot) and **YOLO** (fine-tuned weapons: gun/knife/grenade/explosive)
- Returns bounding boxes, timestamps, per-label counts, `weapon_detected` flag

## Detection types (important design note)
Targets split into **two different AI problems**:

| Target | Type | Technique | Difficulty |
|---|---|---|---|
| Bomb, gun, knife | Object | Object detector (YOLO/OWLv2) — already have pipeline | 🟢 Easy |
| Fighting | Action | Video action recognition (temporal model) | 🟡 Medium |
| Snatching/Robbery | Action | Video action recognition | 🟠 Hard |
| Killing | Action | Video action recognition (map → Shooting) | 🔴 Very hard |
| Harassment | Action | Video action recognition (map → Assault/Abuse) | 🔴 Very hard |

**Datasets:** UCF-Crime (best match — fighting, robbery, shooting, explosion, assault, abuse), RWF-2000, Real-Life-Violence-Situations, Hockey Fight.

---

## PHASE 0 — Design decisions
- [ ] 0.1 Split targets into **Objects** (bomb/gun/knife → object detector) vs **Actions** (fighting/snatching/killing/harassment → temporal model)
- [ ] 0.2 Lock final label set (e.g. `Fighting`, `Snatching/Robbery`, `Shooting/Killing`, `Explosion`, `Assault/Harassment`, `Normal`)
- [ ] 0.3 Decide fusion: object detector runs alongside action classifier; combine both into one result per video

## PHASE 1 — Action Recognition Model (fighting, snatching, killing, harassment)
- [ ] 1.1 Get datasets — UCF-Crime, RWF-2000, Real-Life-Violence-Situations
- [ ] 1.2 Preprocess — sample clips into fixed-length frame sequences, resize, normalize, train/val/test split
- [ ] 1.3 Pick base model — VideoMAE or TimeSformer (HuggingFace) or 3D-CNN (I3D/SlowFast)
- [ ] 1.4 Fine-tune on label set; handle class imbalance (killing/harassment are rare)
- [ ] 1.5 Evaluate — per-class precision/recall/F1, confusion matrix; set per-class confidence threshold
- [ ] 1.6 Export trained model (`.pt` / HF checkpoint) into backend
- [ ] 1.7 Create `services/action_detection.py` → `detect_actions(video_path, num_frames, threshold)` returning per-segment label + score + timestamps

## PHASE 2 — Object Detection for Bomb / Weapons
- [ ] 2.1 Add `bomb` (+ existing gun/knife/grenade/explosive) to YOLO classes or OWLv2 queries in `backend/ml/vid_img.py`
- [ ] 2.2 (If YOLO) source/annotate bomb images or fine-tune; else rely on OWLv2 zero-shot for bomb
- [ ] 2.3 Verify against `weapon_*.mp4` sample clips

## PHASE 3 — Backend Integration
- [ ] 3.1 Register `"action"` / `"violence"` backend in `DETECTION_MODELS` in `routes/detection.py`
- [ ] 3.2 Combined pipeline — run action classifier + object detector, merge into one response
- [ ] 3.3 Extend `schemas/detection.py` — add action segments (label, score, start/end time) alongside object boxes + overall `threat_detected` flag
- [x] 3.4 Add CORS middleware in `main.py` for the Next.js origin
- [ ] 3.5 Write `test_actions.py`

## PHASE 4 — Persistence & History
- [ ] 4.1 Add `DetectionJob` model to Prisma (user, filename, models used, result JSON, detected labels, video URL, createdAt, status)
- [ ] 4.2 Save uploaded video to MinIO + store result row after inference
- [ ] 4.3 `GET /detection/history` (past scans) + `GET /detection/{id}` (full detail)
- [ ] 4.4 (Optional) Async jobs — return `job_id`, process in background, poll status

## PHASE 5 — Frontend: Setup
- [x] 5.1 `create-next-app` (App Router + TypeScript + Tailwind) in `frontend/`
- [x] 5.2 Install axios, auth store (zustand/Context), react-hook-form, shadcn/ui
- [x] 5.3 API client + `.env.local` (`NEXT_PUBLIC_API_URL`) + JWT interceptor

## PHASE 6 — Frontend: Auth
- [x] 6.1 Login page → POST `/auth/login` (form-urlencoded), store JWT
- [x] 6.2 Signup page → POST `/auth/signup`
- [x] 6.3 Auth context + token persistence (zustand + localStorage)
- [x] 6.4 Protected routes / redirect to login (dashboard guard)
- [x] 6.5 Logout
- [x] 6.6 Email OTP verification at signup — inactive user until code confirmed; Gmail SMTP + `/verify` page, resend support

## PHASE 7 — Frontend: Dashboard
- [ ] 7.1 Layout (sidebar + top bar, user email)
- [ ] 7.2 Summary cards — total scans + counts per threat type (fights, snatching, bombs…)
- [ ] 7.3 Recent detections list from `GET /detection/history`
- [ ] 7.4 Loading skeletons + empty states

## ⭐ CORE USER FLOW (requested) — Upload → Detect → Marked results
The main feature the user wants, end to end:
1. User uploads a video on the dashboard.
2. Model runs detection (violence / weapons) on the video.
3. App navigates to a **results page** that shows:
   - **When** the violence happens — exact timestamp(s) on a timeline, click to jump to that moment.
   - **The video played back**, with the detected violence **marked** (bounding boxes + labels drawn on the frames).
   - A **marked/annotated video** the user can view and download (violence highlighted).
Covered by Phases 8–9 below, plus these backend additions:
- [ ] C.1 Backend: produce a **server-side annotated (marked) video** — draw boxes/labels on detected frames, re-encode to mp4, store in MinIO, return its URL in the detection response.
- [x] C.2 Backend: return violence **segments/timestamps** grouped (start/end + label + score), not just per-frame hits, so the frontend timeline can jump-to-time. → `/detection/video/llm` (Gemini) and `/detection/video/action` (local VideoMAE) both return `{label, category, description, start_time, end_time, confidence}`.
- [x] C.3 ~~real *action* violence needs Phase 1 training~~ — **superseded**: `/detection/video/action` uses a pretrained UCF-Crime VideoMAE checkpoint (Fighting/Assault/Abuse/Robbery/Shooting), so action violence works without training first. Phase 1 is still the path to a *custom* label set (esp. harassment, which no off-the-shelf model covers — see L.9).

## ⭐ PHASE L — Proper video violence detection via LLM / Vision-LLM (requested)
Goal: detect violence in a video **properly** using an LLM/VLM (vision-language model), not just object boxes, and wire it into the frontend. (Repo already has `run_qwen.py` + `qwen_env` — likely Qwen2-VL experiments to build on.)
- [x] L.1 Pick the VLM approach — **chose hosted Gemini** (`gemini-2.0-flash`) over local Qwen2.5-VL-7B: the RTX 4060's 8 GB VRAM does not fit a 7B VLM at fp16 (~16 GB needed).
- [x] L.2 `services/llm_detection.py` → `detect_violence_llm(video_bytes, mime_type)`: sends the clip inline to Gemini with a response schema, returns per-segment `{label, category, description, start/end time, confidence}` + `violence_detected`.
- [x] L.3 Added `/detection/video/llm` (kept separate from the `/detection/video` object contract).
- [x] L.4 Extended `schemas/detection.py` with `ViolenceSegment` / `LlmDetectionResponse`, coexisting with the YOLO/OWLv2 response.
- [x] L.5 Robustness — 180s timeout, JSON-parse guards, 503 (unconfigured) / 400 (too large) / 502 (upstream) mapping.
- [x] L.6 Frontend — "AI analysis (LLM)" model option + verdict/segments table with timestamps.
- [x] L.7 Findings render on a timeline in `video-upload.tsx` (client-side player, jump-to-time). Independent of the C.1 marked video, which is still open.
- [x] L.8 Documented in `.env.example` (`GEMINI_API_KEY`, `ACTION_MODEL_ID`) and `RUN.md`.
- [ ] L.9 **Harassment** — no off-the-shelf model exists ([HarassGuard, 2026](https://arxiv.org/html/2604.00592) uses VLM + prompt engineering, 88% binary / 68.9% multi-class, on a custom 825-clip set). Currently handled by the Gemini prompt with a `harassment` category. A purpose-built classifier needs Phase 1 training on a labelled set.

## ⭐ PHASE A — Local action recognition (fighting) — DONE
- [x] A.1 `services/action_detection.py` → `detect_actions(video_path, window_seconds, stride_seconds, threshold)`: sliding-window VideoMAE over the clip, merges adjacent same-label windows into segments.
- [x] A.2 `/detection/video/action` route; reuses `LlmDetectionResponse` so both paths share one frontend timeline.
- [x] A.3 Model: `OPear/videomae-large-finetuned-UCF-Crime` (14 UCF-Crime classes), overridable via `ACTION_MODEL_ID`.
- [x] A.4 Frontend "Fighting (local)" option — runs offline, no API key.
- [x] A.5 CUDA torch (`2.10.0+cu126`) so the RTX 4060 is actually used; was silently running the `+cpu` build.

## ⭐ PHASE G — Accurate multi-crime detection + precise time-localization (requested)
Goal: detect **every** crime in a video **correctly**. If a crime happens, pinpoint **exactly when** (e.g. violence at 0:25–0:30 in a 1-min video), **auto-cut that exact clip** and show it; if nothing happens, mark the video **Clear**. Target: **maximum accuracy** (note: no violence model is truly 100% — see G.13).

### Accuracy — an ensemble of specialist models (each crime → its best model)
- [x] G.1 **Dedicated VIOLENCE model** — `cliffer1/videomae-...violence-nonviolence` (binary). Tested: Fight → Violence **99.9%**, Accident/Gun → NonViolence. Add as the fight/assault specialist. *(code written in `services/violence_detection.py`, integration pending)*
- [x] G.2 **Crime/action model** (UCF-Crime) — accidents, robbery, shooting, arson, burglary, vandalism, etc.
- [x] G.3 **Weapon model** (YOLO) — gun / knife / grenade objects.
- [x] G.4 **Ensemble** — run the specialists; each crime type reported by the model that's best at it; combine into one result. Reduces both misses and false positives.
- [ ] G.5 (Highest accuracy, optional) Fine-tune on target datasets (RWF-2000, Real-Life-Violence, UCF-Crime) for these exact crimes.

### Time-localization — WHEN the crime happens
- [x] G.6 Sliding-window classification across the WHOLE video (≈2–3 s window, ~1 s stride) → per-window crime probability + timestamp.
- [x] G.7 Merge consecutive positive windows into the **exact crime segment(s)** (e.g. 0:25–0:30), not the whole video.
- [x] G.8 **Auto-cut the exact crime clip** (ffmpeg) — just those seconds. *(clip cutter already built: `services/clip_extract.py`)*

### Clear handling
- [x] G.9 If no window passes the crime threshold → report the video **Clear** (no crime).

### Output / UI (mostly built in Phase F)
- [x] G.10 Per crime: exact timestamp + cut clip + accurate label + explanation (what + objects/vehicles/weapons involved) + bounding boxes.
- [x] G.11 Results + report pages show the precise crime clip(s).

### Accuracy tuning + honesty
- [ ] G.12 Calibrate each model's threshold to balance precision/recall (fewer false positives AND fewer misses).
- [ ] G.13 **Reality check:** aim for max accuracy; realistically ~85–95% on clear cases, lower on ambiguous/low-quality footage. **True 100% is not achievable by any model** — the goal is "as accurate as possible", documented honestly for the FYP.

## ⭐ PHASE F — Full incident system: detect → extract clip → explain (requested)
Goal: for every detected incident (violence / accident / crime), **cut out the exact clip** from the uploaded video and **explain it in plain language** in the UI — e.g. "⚠️ Accident — a car and a motorbike collided at 0:05", "⚠️ Violence — two people fighting at 0:02–0:17". Use a proper violence model/LLM.

### Detection (mostly done)
- [x] F.1 Auto-cascade endpoint `/detection/video/auto` — Gemini → local action model → Qwen; first model to detect wins; returns segments with start/end timestamps + category + confidence.
- [x] F.2 Accurate labels — normalise confusable violence classes (Abuse/Assault/Fighting → "Fighting / Assault"), accidents → "Road Accident". Categories: violence / theft / harassment / accident / other.
- [ ] F.3 (Optional) Add a dedicated violence model for higher accuracy — e.g. RWF-2000 / Real-Life-Violence CNN, or a HF ViT violence classifier — and fold it into the cascade.

### Clip extraction
- [ ] F.4 Install/use **ffmpeg** on the backend.
- [ ] F.5 `services/clip_extract.py` → cut each incident segment `[start_time, end_time]` (with a small pad) out of the uploaded video into a short mp4.
- [ ] F.6 Store the extracted clips (MinIO, or a local `/media` static dir) and return each clip's URL in the response.

### Explanation (what happened + objects/vehicles involved)
- [ ] F.7 For each incident clip, run a VLM (Gemini when quota, else local Qwen) to produce a **natural-language explanation** — what happened + who/what is involved (car, motorbike, person, weapon).
- [ ] F.8 Identify objects/vehicles in the incident — reuse YOLO/OWLv2 (car, bike, gun, knife) or take them from the VLM description.
- [ ] F.9 Extend `schemas/detection.py` per-segment: add `clip_url` and `explanation` fields.

### UI
- [ ] F.10 Results page: for each incident show its **own extracted mini-clip player**, the **explanation text**, category badge, timestamp range, and confidence.
- [ ] F.11 Prominent top verdict, e.g. "⚠️ Accident detected — car & bike collision (0:05)".
- [ ] F.12 Persist clip_url + explanation with the scan (`detection_scans`) so history shows them too.
- [ ] F.13 Download report — include incident clips + explanations.

### Notes
- The RTX 4060 is used for the action model (fast ~4s). To make **Qwen fast too**, install CUDA torch in `qwen_env` (currently `+cpu`) — see F.3/Deploy.
- For the most accurate *specific* labels + explanations, Gemini is best; needs a working-quota key (new project / billing).

## 🖥️ RUN / OPERATIONS
- [x] R.1 Save all commands needed to run the full stack → see [`RUN.md`](RUN.md) (Docker + Postgres on 5434, backend on 8000, frontend on 3000, env/DB creds, DBeaver, troubleshooting).

## PHASE 8 — Frontend: Upload & Model Selection
- [x] 8.1 Drag-and-drop upload + client validation (type, 200MB cap)
- [x] 8.2 Model/mode selector (Weapons/YOLO vs Any-object/OWLv2 + free-text queries) + params (`num_frames`, `threshold`)
- [x] 8.3 Upload progress bar → POST multipart `/detection/video` (with JWT); inline result summary + detections table
- [ ] 8.4 Job polling if async (Phase 4.4) — N/A until async backend exists

## PHASE 9 — Frontend: Results / Detail View
- [ ] 9.1 Verdict banner — e.g. "⚠️ Fighting + Bomb detected" vs "✅ Clear"
- [ ] 9.2 Timeline — action segments with labels/scores, jump-to-timestamp
- [ ] 9.3 Video player with bounding-box overlays for objects (bomb/weapons)
- [ ] 9.4 Detections table + per-label counts chart
- [ ] 9.5 Download report (extend `report.json` shape)

## PHASE 10 — Polish & Deploy
- [ ] 10.1 Error handling, toasts, responsive design
- [ ] 10.2 Dockerize backend + frontend; compose with MinIO + Postgres
- [ ] 10.3 README + setup docs
- [ ] 10.4 End-to-end test: signup → login → upload → view threat detail

---

## ⚠️ FYP honesty notes
1. **Killing** and **harassment** have the weakest training data — expect low accuracy. Consider mapping killing → `Shooting` and harassment → `Assault/Abuse`, and state this in the report.
2. **Bomb** as a still object is detectable; a *bomb going off* is an `Explosion` action — the classifier handles that, the object detector handles the device.

## Recommended execution order
1. Phase 3.4 (CORS) + Phase 5–6 (Next.js scaffold + auth) — no model training needed
2. Phase 1 (action model) — gather UCF-Crime dataset in parallel
3. Phase 2–4 (object detection + integration + persistence)
4. Phase 7–9 (dashboard, upload, results)
5. Phase 10 (polish + deploy)
