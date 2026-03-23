# Skill Registry — Docker image
# Builds a minimal Python image with the skillctl package installed.

FROM python:3.12-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered output.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Create a non-root user for running the application.
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --create-home appuser

WORKDIR /app

# Install dependencies first (layer caching).
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Copy the full source and reinstall so the package picks up all modules.
COPY . .
RUN pip install --no-cache-dir .

# Persistent data lives here; mount a volume at runtime.
RUN mkdir -p /data && chown appuser:appuser /data
VOLUME /data

EXPOSE 8080

USER appuser

CMD ["skillctl", "serve", "--host", "0.0.0.0", "--port", "8080", "--data-dir", "/data"]
