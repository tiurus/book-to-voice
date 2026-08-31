#!/usr/bin/env bash
set -euo pipefail

remote="home"
remote_dir="services/book-to-voice"

ssh "$remote" "mkdir -p '$remote_dir'"
rsync -az \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude 'data/audio/*' \
  --exclude 'data/models/*' \
  ./ "$remote:$remote_dir/"
ssh "$remote" "cd '$remote_dir' && sudo docker compose up --build -d && sudo docker compose ps"
ssh "$remote" "curl --fail --retry 36 --retry-delay 5 --retry-connrefused --retry-all-errors http://127.0.0.1:8000/api/health"
