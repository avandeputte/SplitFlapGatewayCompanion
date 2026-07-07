# SplitFlapGatewayCompanion — single-stage image (Phase 1).
# The Phase-1 frontend is static (no build step), so no Node stage is needed yet.
# When the SPA gains a build step (React/Vite in a later phase) this becomes a
# multi-stage build: a Node stage compiles the SPA, the Python stage serves it.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    COMPANION_DATA_DIR=/data \
    PYTHONPATH=/app/backend

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/
COPY apps/ ./apps/
COPY VERSION ./VERSION

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

# Binds 0.0.0.0 by default (see app/__main__.py); honors COMPANION_HOST/PORT.
CMD ["python", "-m", "app"]
