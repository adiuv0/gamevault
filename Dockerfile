# ── Stage 1: Build frontend ──────────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Production ─────────────────────────────────────────────────────
FROM python:3.12-slim
WORKDIR /app

# System dependencies for Pillow image processing + lxml + gosu for privilege drop
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo-dev \
    libpng-dev \
    libwebp-dev \
    libxml2-dev \
    libxslt1-dev \
    gcc \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (separate layer for Docker caching)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip cache purge \
    && apt-get purge -y --auto-remove gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy backend code
COPY backend/ ./backend/

# Copy built frontend from stage 1
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Create data directory and non-root user
RUN mkdir -p /data/library \
    && groupadd -r gamevault && useradd -r -g gamevault -d /app gamevault \
    && chown -R gamevault:gamevault /app /data

# Entrypoint fixes permissions on mounted volumes, then drops to gamevault user
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

# Environment defaults
ENV GAMEVAULT_DATA_DIR=/data
ENV GAMEVAULT_LIBRARY_DIR=/data/library
ENV GAMEVAULT_DB_PATH=/data/gamevault.db
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

# ── Image metadata ─────────────────────────────────────────────────────────
LABEL org.opencontainers.image.title="GameVault" \
      org.opencontainers.image.description="Self-hosted game screenshot manager. Import from Steam, upload, annotate, search, and share." \
      org.opencontainers.image.url="https://github.com/adiuv0/gamevault" \
      org.opencontainers.image.source="https://github.com/adiuv0/gamevault" \
      net.unraid.docker.templateurl="https://raw.githubusercontent.com/adiuv0/unraid-templates/main/templates/gamevault.xml"

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')" || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080"]
