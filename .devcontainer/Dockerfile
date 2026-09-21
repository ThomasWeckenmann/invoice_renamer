FROM mcr.microsoft.com/devcontainers/python:3.12

# Package list mirrors README.md's Linux prerequisites (Tauri's webview
# toolchain and Tesseract OCR), plus rsync for scripts/linux_workspace.sh.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    rsync \
    tesseract-ocr \
    tesseract-ocr-deu \
    libwebkit2gtk-4.1-dev \
    curl \
    wget \
    file \
    libssl-dev \
    libayatana-appindicator3-dev \
    librsvg2-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspaces
