FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    HELMET_MODEL_PATH=/workspace/models/best.pt \
    PERSON_MODEL_PATH=/workspace/models/yolov8n.pt \
    FASTREID_WEIGHTS_PATH=/workspace/models/market_bot_R50.pth \
    FONT_PATH=/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fonts-noto-cjk \
        libglib2.0-0 \
        tini \
    && curl -fsSL \
        https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
        -o /usr/local/bin/cloudflared \
    && chmod +x /usr/local/bin/cloudflared \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

COPY backend/requirements.txt ./requirements.txt

# Match torchvision to the CUDA-enabled torch already provided by the base
# image before installing Ultralytics and the application dependencies.
RUN python -m pip install --upgrade pip \
    && python -m pip install \
        --index-url https://download.pytorch.org/whl/cu124 \
        torchvision==0.20.1 \
    && python -m pip install -r requirements.txt

COPY backend/app ./app
COPY backend/scripts ./scripts
COPY backend/start-production.sh ./start-production.sh

RUN chmod +x ./start-production.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["./start-production.sh"]
