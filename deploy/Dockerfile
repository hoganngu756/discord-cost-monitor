# =============================================================================
# Multi-stage Dockerfile for Kube Cost Bot
# Stage 1: Install dependencies into a virtualenv
# Stage 2: Copy virtualenv into a minimal Alpine runtime image
# =============================================================================

# ── Stage 1: Builder ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install dependencies into an isolated virtualenv
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt


# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:3.12-alpine AS runtime

# Security: run as non-root user
RUN addgroup -S botgroup && adduser -S botuser -G botgroup

WORKDIR /app

# Copy the virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Copy application source
COPY bot/ ./bot/
COPY metrics/ ./metrics/
COPY config/ ./config/

# Switch to non-root user
USER botuser

# Health check — verify Python can import the entrypoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "from bot.main import KubeCostBot" || exit 1

# Entrypoint
CMD ["python", "-m", "bot.main"]
