# ── Stage 1: Builder ──────────────────────────────────────────────────────
FROM python:3.11.9-alpine AS builder

WORKDIR /build

COPY requirements.txt .

# Install into an isolated directory so we can copy it cleanly
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: Runtime ──────────────────────────────────────────────────────
FROM python:3.11.9-alpine AS runtime

RUN addgroup -g 1001 appgroup && \
    adduser -D -u 1001 -G appgroup appuser

WORKDIR /app

# Copy only the installed packages from the builder — nothing else crosses over
COPY --from=builder /install /usr/local

COPY --chown=appuser:appgroup . .

USER appuser

EXPOSE 5000

CMD ["gunicorn", "--preload", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "60", "app:app"]