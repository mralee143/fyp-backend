# Asynchronous analysis pipeline

How a video goes from upload to report without the browser ever blocking on a
model.

## Shape

```
browser ──POST /detection/jobs──► API ──► MinIO (video bytes)
                                   │
                                   ├──► Postgres  videos + analysis_jobs
                                   └──► Redis     ARQ queue
         ◄──202 {job_id}────────────┘   (~1s, no model has run yet)

                              ARQ worker picks the job up
                                   │
                                   ├─ 5-30%  frames ─► MinIO ─► images row
                                   ├─ 30-70% detection models
                                   ├─ 70-90% incident clips + boxes
                                   └─ 90-100% captions
                                   │
                              job_events (Postgres, append-only)
                                   │
                    ┌──────────────┴──────────────┐
              Redis pub/sub                  webhook dispatch
                    │                              │
         SSE ──► browser                    POST ──► your server
```

`job_events` is the single source of truth. The browser and a webhook consumer
receive byte-identical payloads; only the transport differs, because a browser
cannot receive an inbound HTTP callback.

## Why frames come first

The worker samples, uploads and announces preview frames *before* loading any
model. Decoding a dozen frames takes under a second, while a vision model can
take from five seconds to several minutes. Measured on this machine with
`weapon_gun.mp4`:

```
 0.0s  job.queued
 0.0s  job.started
 1.0s  frame.ready    7%   ← first image on screen
 2.1s  frame.ready   30%   ← all 12 frames on screen
 2.2s  job.progress  40%   YOLO starts
 7.6s  job.completed 100%
```

The user sees real content at 1.0s instead of a spinner until 7.6s.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/detection/jobs` | Upload + queue. Returns `202` with a `job_id`. |
| `GET` | `/detection/jobs/{id}/events` | SSE progress stream (the live channel). |
| `GET` | `/detection/jobs/{id}` | Status snapshot + every frame so far (polling fallback). |
| `GET` | `/detection/jobs/{id}/frames` | All stored frames with captions. |
| `GET` | `/detection/jobs` | Recent jobs. |
| `POST` | `/detection/jobs/{id}/retry` | Re-queue a failed job against the stored video. |
| `GET` | `/detection/scans/{id}/chat` | Conversation + tailored openers. |
| `POST` | `/detection/scans/{id}/chat/messages` | Ask about the analysed video. |
| `POST` | `/webhooks/endpoints` | Register a URL (returns the signing secret **once**). |
| `GET` | `/webhooks/deliveries` | Delivery attempts, for debugging. |

The original blocking endpoints (`/detection/video`, `/video/llm`, `/video/auto`,
…) still work and now write to the normalized tables too.

## Events

`job.queued`, `job.started`, `job.progress`, `frame.ready`, `segment.ready`,
`job.completed`, `job.failed`.

`frame.ready` carries the image with a ready-to-render presigned URL:

```json
{
  "sequence": 4, "event": "frame.ready", "progress": 15,
  "job_id": "e892fd8e-…", "message": "Frame 5 of 12",
  "image": {
    "id": 39, "sequence": 4, "captured_at_seconds": 1.6,
    "caption": "grenade (96%) at 0:01",
    "url": "http://localhost:9000/vision-media/frames/32/0004.jpg?X-Amz-…"
  }
}
```

`segment.ready` carries the incident plus its cut clip and boxed clip.

### Reconnecting

Every event has a per-job `sequence`, sent as the SSE `id:`. On a dropped
connection the browser resends it as `Last-Event-ID` and the server replays only
what came after. The subscription is opened *before* history is replayed, which
closes the window where an event published between the two could be lost.

## Webhooks

```
X-Vision-Event:     frame.ready
X-Vision-Delivery:  1421
X-Vision-Attempt:   1
X-Vision-Signature: t=1786…,v1=9f2c…
```

Verify over the **raw** body — re-serialising the JSON changes the bytes and the
signature will not match:

```python
import hashlib, hmac, time

def verify(secret, raw_body, header, tolerance=300):
    parts = dict(p.split("=", 1) for p in header.split(","))
    if abs(time.time() - int(parts["t"])) > tolerance:
        return False                      # too old — likely a replay
    expected = hmac.new(
        secret.encode(), f'{parts["t"]}.{raw_body}'.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, parts["v1"])
```

The timestamp is inside the signed string, so a captured request cannot be
replayed later. Failed deliveries retry with backoff (2s, 8s, 30s, 120s) up to
`WEBHOOK_MAX_ATTEMPTS`.

## Caching

`services/cache.py` caches dashboard stats, history and finished reports in
Redis under a per-user namespace:

```
v1:{user_generation}:{namespace}:{user_id}:{suffix}
```

Invalidation bumps the user's generation counter with one `INCR`, which
logically expires every key for that user without scanning the keyspace. The
worker bumps it when a job finishes.

Cached report payloads embed presigned URLs, so their TTL (120s) is deliberately
far below the signature lifetime (3600s) — a cache hit can never hand the
browser a dead link.

Redis is optional everywhere. If it is down, `cached_json` calls the loader
directly, the SSE endpoint falls back to polling `job_events`, and only the queue
genuinely stops working.

## The chat agent

`POST /detection/scans/{id}/chat/messages` answers questions about one analysed
video. The prompt carries the stored analysis (summary, incidents with
timestamps, per-frame captions) *and* the frame images, so questions about the
scene are answered by looking rather than guessing.

Three backends, in order: Gemini (multimodal), the local Qwen server (text
only), then a plain readout of the stored analysis. The response's `source`
field says which one answered, and the UI labels a fallback reply rather than
passing it off as reasoning.

Answers name the segments and frames they used; those are written to
`chat_citations` and rendered under the reply.

This is separate from `/chat`, the general tool-using agent that runs detection
on an ad-hoc upload. This one is read-only and scoped to a stored scan.
