#!/usr/bin/env bash
set -e
# PulseAudio fournit un périphérique audio virtuel par réunion (null sink).
rm -rf /tmp/pulse-* ~/.config/pulse
pulseaudio -D --exit-idle-time=-1 --disallow-exit --log-level=error || true
sleep 1
exec python -m bot.manager
