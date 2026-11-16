# Image officielle Playwright : Chromium et ses dépendances sont déjà installés.
FROM mcr.microsoft.com/playwright/python:v1.65.2-noble

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb pulseaudio pulseaudio-utils ffmpeg x11vnc novnc websockify fonts-dejavu-core fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt bot/requirements.txt
RUN pip install --break-system-packages -r bot/requirements.txt
COPY . bot/

EXPOSE 6080
CMD ["bash", "bot/entrypoint.sh"]
