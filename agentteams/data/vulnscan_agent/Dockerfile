# agentteams vulnscan agent image: goose + agentteams, no credentials, non-root.
#
# Built by `python -m agentteams.vulnscan agent-image build`. That command builds the agentteams
# wheel from your checkout into ./dist and passes the build args below.
#
# Supply chain:
#   * PYTHON_IMAGE should be pinned by digest (python:3.12-slim@sha256:...). The build command
#     warns when it is a bare tag.
#   * The goose CLI release tarball is pinned by GOOSE_VERSION *and* GOOSE_SHA256. A mismatch
#     fails the build. Take the checksum from the release page, never from this file.
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

ARG GOOSE_VERSION
ARG GOOSE_SHA256
ARG TARGETARCH
RUN test -n "$GOOSE_VERSION" && test -n "$GOOSE_SHA256" \
 || { echo "GOOSE_VERSION and GOOSE_SHA256 build args are required" >&2; exit 1; }

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl bzip2 libxcb1 libdbus-1-3 git \
 && rm -rf /var/lib/apt/lists/*

RUN case "${TARGETARCH:-amd64}" in \
      amd64) GARCH=x86_64-unknown-linux-gnu ;; \
      arm64) GARCH=aarch64-unknown-linux-gnu ;; \
      *) echo "unsupported arch ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
 && curl -fsSL -o /tmp/goose.tar.bz2 \
      "https://github.com/block/goose/releases/download/v${GOOSE_VERSION}/goose-${GARCH}.tar.bz2" \
 && echo "${GOOSE_SHA256}  /tmp/goose.tar.bz2" | sha256sum -c - \
 && mkdir -p /tmp/goose && tar -xjf /tmp/goose.tar.bz2 -C /tmp/goose \
 && install -m 0755 "$(find /tmp/goose -type f -name goose | head -1)" /usr/local/bin/goose \
 && rm -rf /tmp/goose /tmp/goose.tar.bz2 \
 && goose --version

COPY dist/*.whl /tmp/wheels/
RUN pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels

# The agent's home is a tmpfs at run time (the rootfs is read-only); nothing persists between runs.
RUN useradd --uid 10001 --no-create-home --home-dir /agenthome --shell /usr/sbin/nologin agent \
 && mkdir -p /agenthome && chown 10001:10001 /agenthome
USER 10001:10001
WORKDIR /work
ENV HOME=/agenthome PYTHONDONTWRITEBYTECODE=1
