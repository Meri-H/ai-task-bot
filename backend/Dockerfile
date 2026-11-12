FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

# ffmpeg is not required: faster-whisper decodes OGG/Opus itself (PyAV), and OpenAI accepts OGG.
WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# INSTALL_LOCAL_WHISPER=true bakes the offline transcription model runtime into the image.
ARG INSTALL_LOCAL_WHISPER=true
RUN if [ "$INSTALL_LOCAL_WHISPER" = "true" ]; then pip install ".[local]"; else pip install .; fi

RUN useradd --create-home --uid 1000 app && mkdir -p /models && chown app /models
USER app
ENV WHISPER_MODEL_DIR=/models

EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
