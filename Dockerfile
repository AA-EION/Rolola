# syntax=docker/dockerfile:1.7
# Rolola — one container: web app + mpv audio engine + yt-dlp.
# Multi-arch (linux/amd64, linux/arm64): every base image below is multi-arch.

# Base images are build args so you can point them at a registry mirror.
ARG DEBIAN_IMAGE=debian:trixie-20260918-slim
ARG DENO_IMAGE=denoland/deno:bin-2.9.6

# Deno is the JavaScript runtime yt-dlp needs to solve YouTube's player challenges.
FROM ${DENO_IMAGE} AS deno

FROM ${DEBIAN_IMAGE} AS base
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip \
      mpv ffmpeg alsa-utils ca-certificates tzdata tini curl \
 && rm -rf /var/lib/apt/lists/* /usr/share/man/* \
 && find /usr/share/doc -type f ! -name copyright -delete   # keep license files (GPL/LGPL)

FROM base AS build
COPY requirements.txt /tmp/requirements.txt
# Optional: `--secret id=ca_bundle,src=corp-ca.pem` when building behind a TLS-inspecting proxy.
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -s /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi \
 && python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-compile -r /tmp/requirements.txt

FROM base
LABEL org.opencontainers.image.title="Rolola" \
      org.opencontainers.image.description="Rolola: the shared office jukebox" \
      org.opencontainers.image.source="https://github.com/AA-EION/Rolola" \
      org.opencontainers.image.licenses="LicenseRef-PolyForm-Noncommercial-1.0.0"
COPY --from=deno /deno /usr/local/bin/deno
COPY --from=build /opt/venv /opt/venv
# 568 is the default "apps" user on TrueNAS SCALE; override with --user if needed.
RUN groupadd -g 568 rolola && useradd -u 568 -g 568 -G audio -d /data -M -s /usr/sbin/nologin rolola \
 && mkdir -p /data /music && chown 568:568 /data
COPY rolola /app/rolola
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    DATA_DIR=/data \
    MUSIC_DIR=/music \
    PORT=8080 \
    HOME=/data \
    XDG_CACHE_HOME=/data/cache/xdg
WORKDIR /app
USER 568:568
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-m", "rolola"]
