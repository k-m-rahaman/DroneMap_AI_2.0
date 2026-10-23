# CPU runtime for the existing application. No private data enters this image.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    YOLO_AUTOINSTALL=false \
    YOLO_CONFIG_DIR=/tmp/dronemap-yolo \
    MPLCONFIGDIR=/tmp/dronemap-matplotlib \
    DRONEMAP_HOSTED=1 \
    DRONEMAP_DATA_DIR=/var/lib/dronemap \
    PORT=8000

RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgdal-dev build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 dronemap \
    && useradd --uid 10001 --gid dronemap --create-home dronemap \
    && mkdir -p /app /var/lib/dronemap \
    && chown dronemap:dronemap /app /var/lib/dronemap

WORKDIR /app
COPY requirements.txt requirements-ai.txt ./
# Select official CPU wheels first; the application requirements retain pins.
RUN python -m pip install --no-cache-dir torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install --no-cache-dir -r requirements-ai.txt
COPY main.py hosting.py storage.py imagery.py geospatial.py spatial_quality.py missions.py reports.py public_demo.py ./
COPY index.html my_logo.png drone_bg.mp4 best.pt ./
COPY static/ ./static/
COPY fonts/ ./fonts/
COPY tools/serve_hosted.py ./tools/serve_hosted.py
COPY tools/backup_workspace.py ./tools/backup_workspace.py

USER dronemap
EXPOSE 8000
CMD ["python", "tools/serve_hosted.py"]
