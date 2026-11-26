# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# System deps for compiled packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev libffi-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install into /build/wheels for copy to runtime
RUN pip install --upgrade pip && \
    pip wheel --no-cache-dir --wheel-dir /build/wheels -r requirements.txt

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL maintainer="trading-bot"
LABEL description="Telegram Signal Copy-Trading Bot"

WORKDIR /app

# Only runtime system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 libssl3 \
    && rm -rf /var/lib/apt/lists/*

# Copy wheels from builder and install
COPY --from=builder /build/wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/* \
    && rm -rf /wheels

# Download spaCy model (small English model for NLP fallback)
RUN python -m spacy download en_core_web_sm || true

# Copy source
COPY . .

# Create directories
RUN mkdir -p logs sessions

# Non-root user for security
RUN useradd --create-home --shell /bin/bash botuser && \
    chown -R botuser:botuser /app
USER botuser

# Health check — just verifies Python works
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "print('healthy')"

CMD ["python", "main.py"]
