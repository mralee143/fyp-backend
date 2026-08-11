# Database normalization to Third Normal Form

This document justifies the schema in `prisma/schema.prisma` table by table, and
records what the old design got wrong.

## The problem with the old schema

Everything about an analysis lived in one row of `detection_scans`:

```sql
CREATE TABLE detection_scans (
    id                INT PRIMARY KEY,
    user_id           INT,
    filename          TEXT,
    model             TEXT,       -- 'yolo' | 'owlv2' | 'llm' | ... repeated as a string
    violence_detected BOOLEAN,
    summary           TEXT,
    labels            TEXT[],     -- an ARRAY of label names
    result            JSONB,      -- segments, detections and label_counts, nested
    created_at        TIMESTAMP
);
```

Four concrete defects:

| Defect | Where | Consequence |
| --- | --- | --- |
| Repeating group in a column | `labels TEXT[]` | Breaks **1NF**. "Every scan tagged `Gun`" needs `unnest()` or an array scan — no index helps, and a typo'd label can never be corrected in one place. |
| Nested relations in a blob | `result JSONB` | Breaks **1NF**. Segments and object boxes are separate entities with their own attributes; buried in JSON they cannot be constrained, indexed, joined or aggregated. |
| Derived data stored | `result->'label_counts'` | Breaks **3NF**. It is a `COUNT(*) GROUP BY label` over the detections in the same blob. Two copies of one fact drift apart. |
| Free-text keys | `model TEXT` | Renaming a model, or recording that it runs locally, means an `UPDATE` across every historical row (an update anomaly). |

## The normalized design

```
users
  └── videos                       (user_id)
        └── analysis_jobs          (video_id, model_id)  ── job_queries
              └── scans            (job_id, model_id)
                    ├── segments   (scan_id, label_id, category_id)
                    ├── detections (scan_id, label_id, image_id)
                    ├── scan_labels(scan_id, label_id)          M:N
                    └── chat_sessions └── chat_messages └── chat_citations
        └── images                 (video_id, segment_id)

detection_models · incident_categories · labels          lookup tables
job_events → webhook_deliveries → webhook_endpoints ── webhook_subscriptions
```

### Table by table

**`users`** — unchanged. Key: `id`. `email` is a candidate key (`UNIQUE`).

**`detection_models`, `incident_categories`, `labels`** — lookup tables. Each
name or code is stored exactly once and referenced by id. Renaming a model is
now one `UPDATE` instead of one per scan. `labels` is open-ended (models emit
free text like `"Fighting"` or `"a gun"`); it is interned on write by
`services/reference_data.get_label_id`.

**`videos`** — one uploaded file. `object_key` is the MinIO location; the bytes
never sit in the database. `UNIQUE (user_id, checksum_sha256)` makes re-uploading
the same file a lookup rather than a second copy and a second analysis run.

**`analysis_jobs`** — one run of one model over one video. Holds the lifecycle
(`status`, `stage`, `progress`, timestamps) and every scalar run parameter
(`num_frames`, `score_threshold`, `window_seconds`, `stride_seconds`) — each
depends on the job key alone.

> **Note the absence of `user_id`.** The owner is reachable as
> `job → video → user`. Storing it on the job as well would make `user_id`
> transitively dependent on `video_id`, which is exactly the 3NF violation this
> schema exists to avoid. The same reasoning removes `user_id` from `scans`,
> `segments` and frame `images`.

**`job_queries`** — the OWLv2 free-text query *list*. A list cannot be an array
column without breaking 1NF, so each query is a row ordered by `ordinal`.

**`scans`** — the result of a successful job, 1:1 with it. `model_id` here is the
model that actually *produced* the result; the job's `model_id` is what was
*requested*. For an `auto` run these genuinely differ, so neither is redundant.

**`segments`** — one incident with a time range. Replaces the `segments` array
inside the JSON blob. `UNIQUE (scan_id, ordinal)` fixes the timeline order that
the JSON array previously carried implicitly.

**`detections`** — one object box. `box_xyxy: [x1,y1,x2,y2]` became four numeric
columns (1NF: a coordinate pair is not an atomic value). `image_id` points at the
frame the box was found on.

> `label_counts` is **gone**. It is `SELECT label, COUNT(*) FROM detections GROUP
> BY label`, computed in `scan_repository._build_detail` on read. A derived
> aggregate that is also stored is the textbook 3NF violation.

**`scan_labels`** — the M:N join replacing `labels TEXT[]`. Composite key
`(scan_id, label_id)` with no other columns, so 2NF is satisfied trivially.

**`images`** — every stored image: direct user uploads *and* every frame
extracted from a video, distinguished by `kind`. `caption` is the per-image
summary written while the video is processed — the frame-level counterpart to
`scans.summary`.

Ownership is stored exactly once and a database constraint enforces it:

```sql
ALTER TABLE images ADD CONSTRAINT images_owner_exactly_one
    CHECK ((user_id IS NULL) <> (video_id IS NULL));
```

An `UPLOAD` has a `user_id` and no `video_id`; a `FRAME` has a `video_id` (whose
owner is the user) and no `user_id`. The two can therefore never disagree.
Prisma's schema language cannot express a `CHECK`, so `backend/scripts/migrate_normalize.py`
applies it.

**`chat_sessions` / `chat_messages` / `chat_citations`** — the per-analysis chat
agent. A session belongs to a scan; the owner is `session → scan → job → video →
user`. `chat_citations` records which segments and frames an answer was drawn
from, so a claim can be traced back to evidence.

**`job_events`** — append-only log of everything that happened during a job. One
source of truth feeding both delivery channels (SSE to the browser, webhooks to
servers). `UNIQUE (job_id, sequence)` is what lets a reconnecting SSE client
replay from `Last-Event-ID` with no gap and no duplicate.

**`webhook_endpoints` / `webhook_event_types` / `webhook_subscriptions` /
`webhook_deliveries`** — an endpoint's subscribed events are a join table, not an
`events TEXT[]` column (1NF again). A delivery row stores the *outcome* of an
attempt and references the `job_event`; the body is rebuilt from that event at
send time, so there is no second copy of the payload to drift.

## Documented exceptions

There are no JSON or array columns in the operational schema. Two deliberate
design choices are worth stating so they are not mistaken for oversights:

1. **Enums (`JobStatus`, `ImageKind`, `ChatRole`, `DeliveryStatus`)** are closed
   sets held in the type system rather than in lookup tables. A lookup table
   would add a join and carry no attributes beyond the name. Open-ended sets
   (labels, models, categories) *do* get tables, because they carry attributes
   and change at runtime.
2. **`scans.summary`** could be seen as derivable from the segments. It is not:
   the vision models write a natural-language summary that no query over
   `segments` can reconstruct, so it is an attribute of the scan in its own
   right.

## Migrating

```powershell
cd backend
..\env\Scripts\python.exe -m prisma db push
..\env\Scripts\python.exe scripts\migrate_normalize.py --dry-run   # report only
..\env\Scripts\python.exe scripts\migrate_normalize.py             # backfill
..\env\Scripts\python.exe scripts\migrate_normalize.py --drop-legacy
```

The backfill fans each legacy row out into `videos` + `analysis_jobs` + `scans` +
`segments`/`detections` + `scan_labels`. Because the original video files are
gone, each backfilled video gets a placeholder `legacy/...` object key that
`media_store.resolve_media_url` recognises and reports as "no media". Re-running
is safe: a legacy scan whose video already exists is skipped.

On this database the backfill moved 30 legacy rows into 24 segments, 803 object
detections, 16 interned labels and 74 scan/label links.

After `--drop-legacy`, delete the `DetectionScan` model from
`prisma/schema.prisma` so `db push` does not recreate the table.
