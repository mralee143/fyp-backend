# RUN.md — How to run the project

Two ways to run it:

- **[A. Everything in Docker](#a-everything-in-docker)** — two containers, one command,
  nothing installed on the host. This is what deployment uses.
- **[B. From the venv](#0-one-time-setup)** (section 0 onwards) — the original workflow.
  Faster to iterate on, and the only way to use the RTX 4060 without extra setup.

Commands are for **Windows PowerShell** from the project root `e:\fyp_backend` unless noted.

---

# A. Everything in Docker

**Two images, two containers.**

| Image | Size | What is inside |
|---|---|---|
| `vision-backend` | 5.4 GB | PostgreSQL 14, Redis, MinIO, the FastAPI API, the ARQ worker, and all four detection models |
| `vision-frontend` | 220 MB | The Next.js UI |

The backend image is self-contained: it downloads **nothing** at run time. Verified
by loading every model with `--network none`.

```powershell
# Docker Desktop must be running first
copy .env.example .env      # first time only — then fill in SECRET_KEY, GEMINI_API_KEY, SMTP_*
docker compose up -d --build
```

First build takes **30–50 minutes** — torch, transformers and ~2.1 GB of model
weights. Later builds reuse the layer cache; the weights are in their own stage,
so changing code or even `requirements.txt` does not re-download them.

| URL | What |
|---|---|
| http://localhost:3000 | Frontend |
| http://localhost:8000/docs | API docs |
| http://localhost:9001 | MinIO console |
| `localhost:5434` | PostgreSQL (DBeaver / psql) |

```powershell
docker compose ps                    # health
docker compose logs -f backend       # all five backend processes, one stream
docker compose down                  # stop, keep the data
docker compose down -v               # stop AND wipe database + objects
```

### The five processes inside the backend container

`supervisor` runs them; `docker compose logs backend` shows all five interleaved.

| Process | Port | Notes |
|---|---|---|
| postgres | 5432 | data in the `postgres_data` volume |
| redis | 6379 | queue, cache, SSE pub/sub |
| minio | 9000 / 9001 | objects in the `minio_data` volume |
| api (uvicorn) | 8000 | pushes the Prisma schema before serving |
| worker (arq) | — | waits for the schema, then takes jobs |

```powershell
# status of the five
docker exec vision_backend supervisorctl status
# restart just one
docker exec vision_backend supervisorctl restart worker
```

### Your existing data carries over

The container mounts the same `postgres_data`, `minio_data` and `redis_data`
volumes the old split stack used, at the paths the bundled services expect. The
image ships **PostgreSQL 14** specifically to match — Debian 13 would give 17,
which cannot read a 14 data directory in place.

---

## Deploying to a server

```powershell
copy .env.production.example .env.production
```

Fill in `.env.production`, then **set the four host-dependent values** — the
defaults point at `localhost`, which is the browser's own machine, not the server:

| Variable | Must be |
|---|---|
| `NEXT_PUBLIC_API_URL` | `http://your-server:8000` — baked into the frontend bundle at build time |
| `CORS_ORIGINS` | `http://your-server:3000` — the API rejects any other origin |
| `MINIO_PUBLIC_ENDPOINT` | `your-server:9000` — presigned URLs sign their own Host header |
| `SECRET_KEY` | a fresh random value; the example's is a placeholder |

Then build, push and run:

```powershell
docker compose --env-file .env.production build
docker compose --env-file .env.production push
# on the server:
docker compose --env-file .env.production pull
docker compose --env-file .env.production up -d
```

`BACKEND_IMAGE` / `FRONTEND_IMAGE` in `.env.production` name the images after the
Docker Hub repo, which is what makes `push` and `pull` work with no extra tagging.

> `.env.production` is gitignored — it holds the production `SECRET_KEY`, the SMTP
> app password and the Docker Hub token. `.env.production.example` is the committed
> template. Neither is ever copied into an image (`.dockerignore` excludes `.env*`).

### Things worth knowing

- **The frontend's API URL is baked in at build time.** `NEXT_PUBLIC_API_URL` is
  compiled into the browser bundle, so it must be a *host* address and never the
  compose service name `http://backend:8000` — the browser cannot resolve
  `backend`. Changing it needs a rebuild, not a restart:
  ```powershell
  docker compose build frontend; docker compose up -d frontend
  ```
- **`MINIO_PUBLIC_ENDPOINT`.** Frames and clips reach the browser as presigned
  URLs, and a presigned URL signs its own Host header — so it cannot be rewritten
  after signing and must be signed against the address the browser will request.
  Wrong value = every image in the UI 403s.
- **Ports.** Override `API_PORT`, `FRONTEND_PORT`, `POSTGRES_PORT`, `MINIO_PORT`,
  `REDIS_PORT`, `MINIO_CONSOLE_PORT` in the env file if the host already holds one.
- **CPU by default.** The image installs the CPU torch wheels, so analysis works
  everywhere but is slow. For the GPU you need the [NVIDIA Container
  Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html),
  then set `TORCH_INDEX_URL=https://download.pytorch.org/whl/cu126`, uncomment the
  `deploy.resources` block on `backend` in `docker-compose.yml`, and rebuild.
- **Passwords are reconciled on every start.** Change `POSTGRES_PASSWORD` and
  restart: the entrypoint runs `ALTER ROLE` against the existing cluster. Without
  that, a rotated password would never reach a data directory that predates it.
- **Gemini is the one thing not in the image.** It is a hosted API, and the only
  path that detects harassment. Everything else runs locally.
- **Code changes need a rebuild** (`docker compose up -d --build backend`) — the
  source is copied into the image, not mounted. For a tight edit loop use the venv
  workflow below.

---

# B. From the venv

## 0. One-time setup

> The backend lives in **`backend\`** and is run from there — `main.py`,
> `worker.py` and the `services` / `agentic` packages all resolve relative to
> that directory. The venvs (`env`, `qwen_env`) stay at the repo root and are
> shared, hence the `..\env\` prefixes below.

```powershell
# Backend deps (into the existing venv at .\env)
.\env\Scripts\python.exe -m pip install -r backend\requirements.txt

# Generate the Prisma client (only needed after schema.prisma changes)
cd backend
..\env\Scripts\python.exe -m prisma generate
cd ..

# Frontend deps
cd frontend
npm install
cd ..
```

Environment files:
- Backend config is in **`.env`** (gitignored). Key values used on this machine:
  - `DATABASE_URL=postgresql://root:postgres@localhost:5434/vision_db`
  - `SMTP_*` = Gmail App Password (for signup OTP email)
- Frontend config is in **`frontend/.env.local`**: `NEXT_PUBLIC_API_URL=http://localhost:8000`

---

## 1. Start everything (normal daily startup)

```powershell
# 1) Make sure Docker Desktop is running (start it if not)
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
#    wait ~30s until `docker info` works

# 2) Start ONLY the backing services: Postgres (5434), Redis (6379), MinIO (9000/9001).
#    These sit behind the "split" compose profile, so a bare `docker compose up -d`
#    skips them and starts the all-in-one backend instead — which would fight this
#    workflow for every one of these ports. Naming them explicitly still works:
docker compose up -d postgres redis minio
#    verify:
docker exec vision_db_postgres pg_isready -U root -d vision_db
docker exec vision_db_redis redis-cli ping          # -> PONG
curl http://localhost:9000/minio/health/live        # -> 200

# 3) Make sure port 8000 is free (the ollama container can steal it — see Troubleshooting)
#    (already set to not auto-start, but if it's up:)  docker stop ollama-docker-app-1

# 4) Start the backend (FastAPI on http://localhost:8000)
#    Use the `env` venv (NOT qwen_env — that one is only for the local Qwen model).
#    Run from backend\ — that is the import root.
cd backend
..\env\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
#    (run in its own terminal; leave it running)

# 5) Start the analysis worker — in a NEW terminal.
#    Video analysis runs HERE, not in the API process. Without it, uploads are
#    accepted and stay QUEUED forever. Also from backend\.
cd backend
..\env\Scripts\python.exe -m arq worker.WorkerSettings

# 6) Start the frontend (Next.js on http://localhost:3000) — in a NEW terminal
cd frontend
npm run dev
```

Open **http://localhost:3000**.

### The four processes

| Process | Port | What breaks without it |
| --- | --- | --- |
| Postgres + Redis + MinIO (`docker compose up -d postgres redis minio`) | 5434 / 6379 / 9000 | Everything |
| FastAPI (`uvicorn main:app`) | 8000 | Everything |
| **ARQ worker** (`arq worker.WorkerSettings`) | — | Uploads are accepted but never analysed |
| Next.js (`npm run dev`) | 3000 | The UI |

Redis is optional in the sense that the API still boots without it — the cache
and the live progress stream simply switch off and analysis cannot be queued.
The startup log says so explicitly when that happens.

### After pulling schema changes

```powershell
cd backend
..\env\Scripts\python.exe -m prisma db push            # apply prisma/schema.prisma
..\env\Scripts\python.exe scripts\migrate_normalize.py # backfill legacy scans (one-off)
```

See `docs/NORMALIZATION.md` for what the schema looks like and why.

### Git-Bash: two equivalent ways to run the backend

**Option A — no activation** (run the `env` interpreter directly):
```bash
cd backend
../env/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
```

**Option B — activate first**, then use plain `python`:
```bash
source env/Scripts/activate       # NOT qwen_env
cd backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000
# when done:  deactivate
```

> ⚠️ `env` = backend (FastAPI/Prisma/Gemini). `qwen_env` = local Qwen model only.
> If you activated the wrong one, run `deactivate` and activate `env`.

---

## 2. Health checks

```powershell
# Backend up?  -> {"status":"running", ...}
curl http://localhost:8000/
# API docs:      http://localhost:8000/docs

# Frontend up?
curl -I http://localhost:3000

# Postgres up?
docker exec vision_db_postgres pg_isready -U root -d vision_db
```

---

## 3. Database access (DBeaver / psql)

**Connection settings (this is the app's DB):**
| Field | Value |
|---|---|
| Host | `localhost` |
| **Port** | **`5434`**  ← not 5432 (native Postgres owns 5432/5433) |
| Database | `vision_db` |
| Username | `root` |
| Password | `postgres` |

```powershell
# Quick psql inside the container
docker exec -it vision_db_postgres psql -U root -d vision_db

# List users
docker exec vision_db_postgres psql -U root -d vision_db -c "select id,email,is_active from users order by id;"
```

---

## 4. Stop everything

```powershell
# Stop backend / frontend: Ctrl+C in their terminals

# Stop the database (keeps data in the volume)
docker compose stop
#    or remove the container (data still kept in named volume): docker compose down
#    DANGER — wipes DB data:                                     docker compose down -v
```

---

## 5. Troubleshooting (issues seen on this machine)

- **Backend error `P1001 Can't reach database server at localhost:5434`**
  → Docker Desktop or the Postgres container isn't running. Do steps 1–2 above.

- **`/auth/login` returns 404 or the Ollama page shows on :8000**
  → The `ollama-docker-app-1` container grabbed port 8000. Fix:
  ```powershell
  docker stop ollama-docker-app-1
  docker update --restart=no ollama-docker-app-1   # stop it auto-starting again
  ```
  Then restart the backend (step 4).

- **`password authentication failed for user "root"` on port 5432**
  → That's the *native* Windows PostgreSQL (PostgreSQL 13/18 services), not this project. Use **5434**.

- **Docker: `ports are not available: ... bind: Only one usage of each socket address`**
  → The host already holds that port — usually a `next dev` on 3000 or the venv
  backend on 8000, left over from workflow B. Either stop the host process, or
  move the container:
  ```powershell
  # .env
  FRONTEND_PORT=3001
  # if you move API_PORT, rebuild the frontend so the baked URL matches:
  #   NEXT_PUBLIC_API_URL=http://localhost:<new API_PORT>
  #   docker compose build frontend
  ```
  Both workflows can run side by side this way — they share the same Postgres,
  Redis and MinIO, so the containerised worker will also pick up jobs queued by
  the venv one.

- **Port 3000 already in use / "Another next dev server is already running"**
  → A frontend is already running on 3000 — just use it, or:
  ```powershell
  Get-NetTCPConnection -LocalPort 3000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
  ```

- **Kill whatever holds a port (e.g. 8000):**
  ```powershell
  Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
  ```

---

## 5b. Detection models — which one to use

| UI option | Endpoint | Detects | Needs |
|---|---|---|---|
| Weapons (YOLO) | `/detection/video` | gun / knife / grenade **objects** | — |
| Any object (OWLv2) | `/detection/video` | any free-text object query | — |
| Fighting (local) | `/detection/video/action` | **actions**: fighting, assault, abuse, robbery, shooting | ~1.2 GB model, cached on 1st run |
| AI analysis (LLM) | `/detection/video/llm` | violence, theft, **harassment** + descriptions | `GEMINI_API_KEY`, clip < 15 MB |

- **Objects vs actions**: YOLO/OWLv2 find *things* in a frame. They cannot tell that two people are fighting if no weapon is visible — that needs the action model or the LLM.
- **Harassment** is only detectable via the **LLM** path. There is no off-the-shelf harassment classifier: it is contextual (intent, consent, persistence), and current research does it with vision-language models + prompting. The action model's UCF-Crime classes have no `harassment` label — `Abuse`/`Assault` are the closest proxies.
- Swap the action checkpoint with `ACTION_MODEL_ID` in `.env` (any HF video-classification model; labels are read from its config).

### GPU
The RTX 4060 is used automatically when a **CUDA** torch build is installed. Verify:
```powershell
.\env\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```
`2.10.0+cu126 True` = GPU. If it prints `+cpu False`, the CPU-only wheel got installed (pip's default) and everything runs slowly — reinstall with:
```powershell
.\env\Scripts\python.exe -m pip install --index-url https://download.pytorch.org/whl/cu126 torch==2.10.0+cu126 torchvision==0.25.0+cu126 torchaudio==2.10.0+cu126
```
Stop the backend first — Windows locks the torch DLLs while uvicorn is running.

---

## 6. Notes
- App DB lives in the Docker named volume `fyp_backend_postgres_data` (survives container restarts).
- `docker-compose.yml` maps host **5434 → container 5432** on purpose (native Postgres holds 5432/5433).
- Signup requires **email OTP verification**; login is blocked until the account is verified. SMTP creds live in `.env`.
