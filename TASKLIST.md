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
- [ ] 2.1 Add `bomb` (+ existing gun/knife/grenade/explosive) to YOLO classes or OWLv2 queries in `vid_img.py`
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
- [ ] 5.1 `create-next-app` (App Router + TypeScript + Tailwind) in `frontend/`
- [ ] 5.2 Install axios, auth store (zustand/Context), react-hook-form, shadcn/ui
- [ ] 5.3 API client + `.env.local` (`NEXT_PUBLIC_API_URL`) + JWT interceptor

## PHASE 6 — Frontend: Auth
- [ ] 6.1 Login page → POST `/auth/login` (form-urlencoded), store JWT
- [ ] 6.2 Signup page → POST `/auth/signup`
- [ ] 6.3 Auth context + token persistence
- [ ] 6.4 Protected routes / redirect to login
- [ ] 6.5 Logout

## PHASE 7 — Frontend: Dashboard
- [ ] 7.1 Layout (sidebar + top bar, user email)
- [ ] 7.2 Summary cards — total scans + counts per threat type (fights, snatching, bombs…)
- [ ] 7.3 Recent detections list from `GET /detection/history`
- [ ] 7.4 Loading skeletons + empty states

## PHASE 8 — Frontend: Upload & Model Selection
- [ ] 8.1 Drag-and-drop upload + client validation (type, 200MB cap)
- [ ] 8.2 Model/mode selector (Actions / Weapons+Bomb / Both) + params (`num_frames`, `threshold`)
- [ ] 8.3 Upload progress bar → POST multipart `/detection/video`
- [ ] 8.4 Job polling if async (Phase 4.4)

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
