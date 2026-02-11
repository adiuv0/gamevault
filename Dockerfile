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

# System dependencies for Pillow image processing + lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo-dev \
    libpng-dev \
    libwebp-dev \
    libxml2-dev \
    libxslt1-dev \
    gcc \
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

# Create data directory
RUN mkdir -p /data/library

# Non-root user for security
RUN groupadd -r gamevault && useradd -r -g gamevault -d /app gamevault \
    && chown -R gamevault:gamevault /app /data
USER gamevault

# Environment defaults
ENV GAMEVAULT_DATA_DIR=/data
ENV GAMEVAULT_LIBRARY_DIR=/data/library
ENV GAMEVAULT_DB_PATH=/data/gamevault.db
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

# ── Unraid Docker template labels ─────────────────────────────────────────
# These labels tell Unraid how to render the "Add Container" form
# when pulling directly from Docker Hub.
LABEL org.opencontainers.image.title="GameVault" \
      org.opencontainers.image.description="Self-hosted game screenshot manager. Import from Steam, upload, annotate, search, and share." \
      org.opencontainers.image.url="https://github.com/adiuv0/gamevault" \
      org.opencontainers.image.source="https://github.com/adiuv0/gamevault" \
      net.unraid.docker.managed="dockerman" \
      net.unraid.docker.webui="http://[IP]:[PORT:8080]" \
      net.unraid.docker.icon="https://raw.githubusercontent.com/adiuv0/gamevault/main/frontend/public/vite.svg" \
      net.unraid.docker.shell="sh" \
      net.unraid.docker.overview="Self-hosted game screenshot manager. Import screenshots from Steam, upload manually, annotate, search, and share with OpenGraph link previews." \
      net.unraid.docker.cfg.port.8080.type="Port" \
      net.unraid.docker.cfg.port.8080.name="Web UI Port" \
      net.unraid.docker.cfg.port.8080.target="8080" \
      net.unraid.docker.cfg.port.8080.default="8080" \
      net.unraid.docker.cfg.port.8080.mode="tcp" \
      net.unraid.docker.cfg.port.8080.description="Port for the GameVault web interface." \
      net.unraid.docker.cfg.port.8080.display="always" \
      net.unraid.docker.cfg.port.8080.required="true" \
      net.unraid.docker.cfg.path.data.type="Path" \
      net.unraid.docker.cfg.path.data.name="App Data" \
      net.unraid.docker.cfg.path.data.target="/data" \
      net.unraid.docker.cfg.path.data.default="/mnt/user/appdata/gamevault" \
      net.unraid.docker.cfg.path.data.mode="rw" \
      net.unraid.docker.cfg.path.data.description="Database, config, and thumbnails." \
      net.unraid.docker.cfg.path.data.display="always" \
      net.unraid.docker.cfg.path.data.required="true" \
      net.unraid.docker.cfg.path.library.type="Path" \
      net.unraid.docker.cfg.path.library.name="Screenshot Library" \
      net.unraid.docker.cfg.path.library.target="/data/library" \
      net.unraid.docker.cfg.path.library.default="/mnt/user/screenshots/gamevault" \
      net.unraid.docker.cfg.path.library.mode="rw" \
      net.unraid.docker.cfg.path.library.description="Full-size screenshot storage. Point to your array for large collections." \
      net.unraid.docker.cfg.path.library.display="always" \
      net.unraid.docker.cfg.path.library.required="true" \
      net.unraid.docker.cfg.var.secret.type="Variable" \
      net.unraid.docker.cfg.var.secret.name="Secret Key" \
      net.unraid.docker.cfg.var.secret.target="GAMEVAULT_SECRET_KEY" \
      net.unraid.docker.cfg.var.secret.default="" \
      net.unraid.docker.cfg.var.secret.description="Random string for auth tokens. Generate: python -c \"import secrets; print(secrets.token_hex(32))\"" \
      net.unraid.docker.cfg.var.secret.display="always" \
      net.unraid.docker.cfg.var.secret.required="true" \
      net.unraid.docker.cfg.var.secret.mask="true" \
      net.unraid.docker.cfg.var.baseurl.type="Variable" \
      net.unraid.docker.cfg.var.baseurl.name="Base URL" \
      net.unraid.docker.cfg.var.baseurl.target="GAMEVAULT_BASE_URL" \
      net.unraid.docker.cfg.var.baseurl.default="http://localhost:8080" \
      net.unraid.docker.cfg.var.baseurl.description="Public URL for share links. Example: http://192.168.1.100:8080" \
      net.unraid.docker.cfg.var.baseurl.display="always" \
      net.unraid.docker.cfg.var.baseurl.required="true" \
      net.unraid.docker.cfg.var.disableauth.type="Variable" \
      net.unraid.docker.cfg.var.disableauth.name="Disable Auth" \
      net.unraid.docker.cfg.var.disableauth.target="GAMEVAULT_DISABLE_AUTH" \
      net.unraid.docker.cfg.var.disableauth.default="false" \
      net.unraid.docker.cfg.var.disableauth.description="Set true to disable auth. Only for LAN-only setups." \
      net.unraid.docker.cfg.var.disableauth.display="always" \
      net.unraid.docker.cfg.var.disableauth.required="false" \
      net.unraid.docker.cfg.var.tz.type="Variable" \
      net.unraid.docker.cfg.var.tz.name="Timezone" \
      net.unraid.docker.cfg.var.tz.target="TZ" \
      net.unraid.docker.cfg.var.tz.default="America/Chicago" \
      net.unraid.docker.cfg.var.tz.description="Container timezone." \
      net.unraid.docker.cfg.var.tz.display="always" \
      net.unraid.docker.cfg.var.tz.required="false"

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')" || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080"]
