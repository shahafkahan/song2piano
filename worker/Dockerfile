FROM python:3.11-slim

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TORCH_HOME=/models/torch \
    MODEL_CACHE_DIR=/models

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY shared /app/shared
RUN pip install --no-cache-dir /app/shared

RUN pip install --no-cache-dir --index-url "${TORCH_INDEX_URL}" \
    torch==2.5.1 torchaudio==2.5.1

COPY worker/requirements.txt /app/worker/requirements.txt
RUN pip install --no-cache-dir -r /app/worker/requirements.txt \
    && pip install --no-cache-dir --no-deps basic-pitch==0.4.0

COPY worker/app /app/app

CMD ["celery", "-A", "app.celery_app", "worker", "--loglevel=info", "--concurrency=1", "-Q", "transcription"]
