# agentteams vulnscan TOOLS image: git, the OpenSSH client, netcat (ssh's ProxyCommand), Node.js + npm, the egress
# proxy and the isolation probe. No credentials and no agentteams code. The executor runs it read-only, with every
# capability dropped, no-new-privileges and the operator's uid (agentteams/vulnscan/subproc_confine.py).
#
# Built by `python -m agentteams.vulnscan tools-image build` (scripts/vulnscan-tools-image.sh resolves and checks
# the pins first). Supply chain:
#   * PYTHON_IMAGE should be pinned by digest (python:3.12-slim@sha256:...); the build command warns otherwise.
#     Debian packages (git, git-lfs, openssh-client, netcat-openbsd) come from the base image's signed apt sources.
#   * Node.js is the official linux tarball, pinned by NODE_VERSION and a sha256 per architecture; a mismatch
#     fails the build. The pins live in agentteams/vulnscan/tools_image.py (NODE_*), taken from the release's
#     GPG-signed SHASUMS256.txt, and respect the Rule S-10 release cooldown (>= 14 days old when pinned).
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
ARG NODE_VERSION
ARG NODE_SHA256_AMD64
ARG NODE_SHA256_ARM64
ARG TARGETARCH
RUN test -n "$NODE_VERSION" && test -n "$NODE_SHA256_AMD64" && test -n "$NODE_SHA256_ARM64" \
 || { echo "NODE_VERSION, NODE_SHA256_AMD64 and NODE_SHA256_ARM64 build args are required" >&2; exit 1; }
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git git-lfs openssh-client netcat-openbsd xz-utils \
 && rm -rf /var/lib/apt/lists/*
RUN case "${TARGETARCH:-amd64}" in \
      amd64) NARCH=x64; NSHA="$NODE_SHA256_AMD64" ;; \
      arm64) NARCH=arm64; NSHA="$NODE_SHA256_ARM64" ;; \
      *) echo "unsupported arch ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
 && curl -fsSL -o /tmp/node.tar.xz "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${NARCH}.tar.xz" \
 && echo "${NSHA}  /tmp/node.tar.xz" | sha256sum -c - \
 && mkdir -p /opt/node && tar -xJf /tmp/node.tar.xz -C /opt/node --strip-components=1 --no-same-owner \
 && ln -s /opt/node/bin/node /usr/local/bin/node && ln -s /opt/node/bin/npm /usr/local/bin/npm \
 && rm -f /tmp/node.tar.xz \
 && node --version && npm --version \
 && apt-get purge -y curl xz-utils && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY egress_proxy.py selftest.py /usr/local/lib/vulnscan/
COPY vulnscan-with-token git-credential-vulnscan /usr/local/bin/
RUN chmod 0644 /usr/local/lib/vulnscan/egress_proxy.py /usr/local/lib/vulnscan/selftest.py \
 && chmod 0755 /usr/local/bin/vulnscan-with-token /usr/local/bin/git-credential-vulnscan \
 && git --version && git lfs version && ssh -V && nc -h 2>&1 | head -1
LABEL org.agentteams.vulnscan.tools-node="${NODE_VERSION}"
# Run time always passes --user <operator uid>:<gid>; this default only keeps a bare `docker run` from being root.
USER 65534:65534
WORKDIR /tmp
ENV PYTHONDONTWRITEBYTECODE=1 NPM_CONFIG_UPDATE_NOTIFIER=false NPM_CONFIG_FUND=false NPM_CONFIG_AUDIT=true
