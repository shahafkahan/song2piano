# SongScribe

An open-source, containerized Songscription clone that converts audio into interactive sheet music (**MusicXML**, **PDF**, **MIDI**, and **isolated stems**) with zero paid APIs.

## Architecture

- **Frontend (`frontend/`)**: React, TypeScript, Vite, Tailwind CSS, `opensheetmusicdisplay` (interactive SVG score + live cursor sync), `wavesurfer.js` (waveform + stem hot-swapping), and a zero-CDN Web Audio MIDI synthesizer.
- **API (`backend/`)**: Lean FastAPI container with **zero ML libraries**. Handles uploads, job metadata, HTTP Range audio streaming, and live SSE progress (`GET /api/jobs/{id}/events`).
- **ML Worker (`worker/`)**: Isolated Python ML container (**ONNX Runtime + PyTorch only, zero TensorFlow**):
  1. **FFmpeg** normalization & MP3 preview encoding
  2. **Demucs (`htdemucs_6s`)** 6-stem separation (`piano`, `guitar`, `vocals`, `bass`, `drums`, `other`)
  3. **ByteDance Piano Transcription** (`piano`) or **Basic Pitch ONNX** (`guitar`, `vocals`, `bass`, `strings`, `other`)
  4. **music21** quantization & MusicXML engraving + **Verovio / PyMuPDF** vector PDF rendering
- **Infrastructure**: PostgreSQL, Redis (Celery queue + Pub/Sub SSE events), and S3-compatible object storage (MinIO locally; Cloudflare R2 or AWS S3 in cloud).

---

## 1. Quick Start — Docker Compose (CUDA / MPS / CPU)

```bash
# Linux / macOS
./scripts/up.sh

# Windows PowerShell
.\scripts\up.ps1
```

Or manually:

```bash
cp .env.example .env
# CPU / macOS Docker:
docker compose up --build

# NVIDIA CUDA GPU:
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

Open **http://localhost:5173** (API docs at **http://localhost:8000/docs**, MinIO console at **http://localhost:9001**).

> **Apple Silicon (MPS):** Docker Desktop on macOS runs Linux containers without MPS passthrough. For native MPS GPU acceleration on macOS, run the worker locally (`DEVICE=mps`) pointing at the containerized Postgres/Redis/MinIO or `.env.local`.

---

## 2. Cloud / Serverless GPU Deployment (Env Vars Only)

Because `api`, `worker`, and `frontend` share zero local state and communicate strictly via PostgreSQL, Redis, and S3, you can deploy the exact same container images to cloud/serverless GPUs (RunPod, Modal, AWS ECS, Fly.io, Cloud Run) using environment variables:

| Variable | Example Cloud Value | Notes |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://user:pass@ep-xxx.neon.tech/songscribe?sslmode=require` | Managed Postgres (Neon, RDS, Supabase) |
| `REDIS_URL` | `rediss://default:token@xxx.upstash.io:6379` | Managed Redis (Upstash, ElastiCache) |
| `CELERY_BROKER_URL` | Same as `REDIS_URL` | Celery task queue |
| `CELERY_RESULT_BACKEND` | Same as `REDIS_URL` | Celery results |
| `STORAGE_BACKEND` | `s3` | S3-compatible object storage |
| `S3_ENDPOINT_URL` | `https://<accountid>.r2.cloudflarestorage.com` | Leave blank for AWS S3 |
| `S3_BUCKET` | `songscribe-prod` | Bucket name |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | Cloud credentials | R2 / IAM keys |
| `S3_USE_SSL` | `true` | TLS enabled in cloud |
| `DEVICE` | `auto` | Auto-detects `cuda`, `mps`, or `cpu` |
| `API_UPSTREAM` | `api.internal:8000` | Used by frontend Nginx reverse proxy |

---

## 3. Native Local Mode (No Docker Required)

For hosts without Docker, SongScribe supports `QUEUE_BACKEND=local` and `STORAGE_BACKEND=local` via `.env.local` using two isolated Python virtual environments (`.venv-api` with zero ML libs, `.venv-worker` with PyTorch CUDA/CPU + ONNX):

```powershell
.\scripts\start-local.ps1
```
