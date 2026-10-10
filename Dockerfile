# Multi-stage production Dockerfile for RepoMedic AI
FROM python:3.11-slim as base

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8000

# Install minimal system dependencies (git for worktrees and patch verification)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    patch \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code and assets
COPY repomedic/ repomedic/
COPY benchmarks/ benchmarks/
COPY skills/ skills/
COPY pyproject.toml .

# Create non-root user for execution security
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Health check matching Render/Docker specs
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# Start FastAPI application
CMD ["sh", "-c", "uvicorn repomedic.api:app --host 0.0.0.0 --port ${PORT}"]
