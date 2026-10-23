# SplitFlapGatewayCompanion — single-stage image.
# The SPA is static (vanilla JS, no build step), so no Node stage is needed. If
# the SPA ever gains a build step (e.g. React/Vite), add a Node stage that
# compiles it and have this Python stage serve the compiled output.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    COMPANION_DATA_DIR=/data \
    PYTHONPATH=/app/backend

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
# tesseract: auto-calibration's OCR — it reads the reel back as a text LINE (strong, measured)
# to anchor and validate the rotation; eng traineddata ships with the Debian package.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/
COPY apps/ ./apps/
COPY VERSION ./VERSION

# No USER directive — deliberately, not by omission. This same image IS the Home
# Assistant add-on runtime (addon/config.yaml `image:` — there is no add-on build and
# no s6/run script), and Supervisor bind-mounts the add-on's data dir at /data owned
# by root; install.sh's bind-mount option and every existing named volume are likewise
# root-owned. A non-root USER therefore breaks settings persistence in all three
# deployments, and the usual fix (an entrypoint that chowns /data, then drops
# privileges) is exactly the init layer this one-image-serves-both-worlds design
# avoids. Revisit only alongside an entrypoint that can chown-then-drop.
VOLUME ["/data"]
# 8000 is the main API/UI port; 7000 is the Vestaboard Local API mirror the app also binds
# (see app/__main__._run_dual), for the ha-vestaboard integration which hardcodes 7000.
EXPOSE 8000 7000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

# Binds 0.0.0.0 by default (see app/__main__.py); honors COMPANION_HOST/PORT.
CMD ["python", "-m", "app"]
