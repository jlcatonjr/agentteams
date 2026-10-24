# agentteams vulnscan agent image: one agent CLI + agentteams, no credentials, non-root.
#
# Built by `python -m agentteams.vulnscan agent-image build [--framework F]`. That command builds the
# agentteams wheel from your checkout into ./dist and passes the build args below.
#
# Driver-parameterised: build arg AGENT_CLI (the framework's runtime driver, agentteams/vulnscan/
# agent_drivers.py) selects the install stage `cli-<AGENT_CLI>`; the final stage adds agentteams and the
# non-root user on top of it. The goose and claude stages are implemented in this release; the others
# fail the build with a clear message. Needs BuildKit (Docker's default builder since Engine 23), which builds only
# the stages the final image uses.
#
# Supply chain:
#   * PYTHON_IMAGE should be pinned by digest (python:3.12-slim@sha256:...). The build command
#     warns when it is a bare tag.
#   * The CLI release asset is pinned by CLI_VERSION *and* CLI_SHA256. A mismatch fails the build.
#     Take the checksum from the release metadata (scripts/vulnscan-agent-image.sh does), never from
#     this file.
ARG PYTHON_IMAGE=python:3.12-slim
ARG AGENT_CLI=goose

FROM ${PYTHON_IMAGE} AS base
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git \
 && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------- goose (release tarball, sha256-pinned)
FROM base AS cli-goose
ARG CLI_VERSION
ARG CLI_SHA256
ARG TARGETARCH
RUN test -n "$CLI_VERSION" && test -n "$CLI_SHA256" \
 || { echo "CLI_VERSION and CLI_SHA256 build args are required" >&2; exit 1; }
RUN apt-get update \
 && apt-get install -y --no-install-recommends bzip2 libxcb1 libdbus-1-3 \
 && rm -rf /var/lib/apt/lists/*
RUN case "${TARGETARCH:-amd64}" in \
      amd64) GARCH=x86_64-unknown-linux-gnu ;; \
      arm64) GARCH=aarch64-unknown-linux-gnu ;; \
      *) echo "unsupported arch ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
 && curl -fsSL -o /tmp/goose.tar.bz2 \
      "https://github.com/block/goose/releases/download/v${CLI_VERSION}/goose-${GARCH}.tar.bz2" \
 && echo "${CLI_SHA256}  /tmp/goose.tar.bz2" | sha256sum -c - \
 && mkdir -p /tmp/goose && tar -xjf /tmp/goose.tar.bz2 -C /tmp/goose \
 && install -m 0755 "$(find /tmp/goose -type f -name goose | head -1)" /usr/local/bin/goose \
 && rm -rf /tmp/goose /tmp/goose.tar.bz2 \
 && goose --version

# ---------------------------------------------------------------- claude (native binary, sha256-pinned)
# CLI_SHA256 is platforms.linux-<arch>.checksum from the release's manifest.json, whose detached GPG
# signature (manifest.json.sig, Anthropic's release key 31DD DE24 DDFA B679 F42D 7BD2 BAA9 29FF 1A7E CACE)
# scripts/vulnscan-agent-image.sh verifies before passing it here.
FROM base AS cli-claude
ARG CLI_VERSION
ARG CLI_SHA256
ARG TARGETARCH
RUN test -n "$CLI_VERSION" && test -n "$CLI_SHA256" \
 || { echo "CLI_VERSION and CLI_SHA256 build args are required" >&2; exit 1; }
RUN case "${TARGETARCH:-amd64}" in \
      amd64) CPLAT=linux-x64 ;; \
      arm64) CPLAT=linux-arm64 ;; \
      *) echo "unsupported arch ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
 && curl -fsSL -o /tmp/claude \
      "https://downloads.claude.ai/claude-code-releases/${CLI_VERSION}/${CPLAT}/claude" \
 && echo "${CLI_SHA256}  /tmp/claude" | sha256sum -c - \
 && install -m 0755 /tmp/claude /usr/local/bin/claude \
 && rm -f /tmp/claude \
 && HOME=/tmp/claude-home DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 claude --version \
 && rm -rf /tmp/claude-home

# ---------------------------------------------------------------- not in this release

FROM base AS cli-copilot
RUN echo "AGENT_CLI=copilot: driver not available in this release" >&2; exit 1

FROM base AS cli-codex
RUN echo "AGENT_CLI=codex: driver not available in this release" >&2; exit 1

FROM base AS cli-grok
RUN echo "AGENT_CLI=grok: driver not available in this release" >&2; exit 1

# ---------------------------------------------------------------- final: agentteams + the non-root user
FROM cli-${AGENT_CLI}
ARG AGENT_CLI
ARG CLI_VERSION
LABEL org.agentteams.vulnscan.agent-cli="${AGENT_CLI}" org.agentteams.vulnscan.agent-cli-version="${CLI_VERSION}"
COPY dist/*.whl /tmp/wheels/
RUN pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels

# The agent's home is a tmpfs at run time (the rootfs is read-only); nothing persists between runs. The
# container's working directory is another empty tmpfs (/work/cwd, set by `docker run -w`), never the team.
RUN useradd --uid 10001 --no-create-home --home-dir /agenthome --shell /usr/sbin/nologin agent \
 && mkdir -p /agenthome && chown 10001:10001 /agenthome
USER 10001:10001
WORKDIR /work
ENV HOME=/agenthome PYTHONDONTWRITEBYTECODE=1
