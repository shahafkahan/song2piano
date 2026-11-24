#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
fi

if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  echo "Detected NVIDIA GPU — launching with CUDA worker overlay..."
  docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build "$@"
else
  echo "No NVIDIA GPU detected (or running on macOS/CPU) — launching standard Docker Compose..."
  docker compose -f docker-compose.yml up --build "$@"
fi
