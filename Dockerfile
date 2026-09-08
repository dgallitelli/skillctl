# Skill Registry — Docker image

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --create-home appuser

WORKDIR /app

# Copy only files needed to build the registry package. Installing the server
# extra is required because FastAPI, uvicorn, multipart parsing, and rate
# limiting are deliberately optional for the core CLI.
COPY pyproject.toml README.md CHANGELOG.md LICENSE MANIFEST.in ./
COPY skillctl ./skillctl
RUN python -m pip install --upgrade pip && \
    pip install ".[server]" && \
    python -c "from skillctl.registry.server import create_app"

RUN mkdir -p /data && chown appuser:appuser /data
VOLUME /data

EXPOSE 8080

USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/v1/health', timeout=3)" || exit 1

# The generated HMAC key is persisted in /data. Production operators should
# instead inject SKILLCTL_HMAC_KEY from a secrets manager.
CMD ["skillctl", "serve", "--host", "0.0.0.0", "--port", "8080", "--data-dir", "/data", "--auto-generate-hmac-key"]
