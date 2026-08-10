# RUN.md — How to run the project

Full stack: **Docker Postgres (5434)** + **FastAPI backend (8000)** + **Next.js frontend (3000)**.
Commands are for **Windows PowerShell** from the project root `e:\fyp_backend` unless noted.

---

## 0. One-time setup

```powershell
# Backend deps (into the existing venv at .\env)
.\env\Scripts\python.exe -m pip install -r requirements.txt

# Generate the Prisma client (only needed after schema.prisma changes)
.\env\Scripts\python.exe -m prisma generate

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

# 2) Start the database (Postgres in Docker, host port 5434)
docker compose up -d
#    verify:
docker exec vision_db_postgres pg_isready -U root -d vision_db

# 3) Make sure port 8000 is free (the ollama container can steal it — see Troubleshooting)
#    (already set to not auto-start, but if it's up:)  docker stop ollama-docker-app-1

# 4) Start the backend (FastAPI on http://localhost:8000)
#    Use the `env` venv (NOT qwen_env — that one is only for the local Qwen model).
.\env\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
#    (run in its own terminal; leave it running)

# 5) Start the frontend (Next.js on http://localhost:3000) — in a NEW terminal
cd frontend
npm run dev
```

Open **http://localhost:3000**.

### Git-Bash: two equivalent ways to run the backend

**Option A — no activation** (run the `env` interpreter directly):
```bash
env/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
```

**Option B — activate first**, then use plain `python`:
```bash
source env/Scripts/activate       # NOT qwen_env
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
