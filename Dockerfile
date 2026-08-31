# syntax=docker/dockerfile:1.7
FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/app
COPY requirements.txt requirements-docker.txt ./
RUN python -m pip install --upgrade pip==25.1.1 \
    && python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.8.0 \
    && python -m pip install -r requirements-docker.txt

COPY app ./app
RUN mkdir -p /srv/data/audio /srv/data/models \
    && useradd --system --uid 10001 --home-dir /srv/app tts \
    && chown -R tts:tts /srv/data

USER tts
ENV TTS_DATA_DIR=/srv/data \
    TTS_TORCH_THREADS=4

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-server-header"]
