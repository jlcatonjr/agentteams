#!/usr/bin/env bash
# Install bubblewrap on a GitHub Ubuntu runner and make sure it can start (#262 item 4).
#
# Ubuntu 23.10+ can restrict unprivileged user namespaces through AppArmor, which bwrap needs. Probe with the
# exact _sandbox_profiles.available() command. On failure, first apply the remedy a stock-Ubuntu user would
# (an unconfined bwrap profile that allows userns, as in scripts/test-sandbox-apparmor-userns.sh), and only
# if that fails relax the sysctl. Either remedy is a warning, and the outcome goes in the step summary.
# Used by the ci.yml jobs test (Ubuntu rows), wasm-live and ubuntu-26-confinement.
set -euo pipefail

# A runner DNS or mirror outage (#442: about 1.5 hours of "Temporary failure resolving") is not a test failure. apt
# retries each download, and a failure here is annotated as CI setup (network), so the checks API tells
# infrastructure red apart from code red without the job log.
apt_opts=(-q -o Acquire::Retries=5 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30)
if ! { sudo apt-get "${apt_opts[@]}" update && sudo apt-get "${apt_opts[@]}" install -y bubblewrap; }; then
  echo "::error title=CI setup (network)::apt could not install bubblewrap; no test ran. Infrastructure, not code: re-run the job (git-procedures §H)."
  exit 1
fi

probe() { bwrap --ro-bind / / --dev /dev --unshare-pid --die-with-parent true; }
knob="$(sysctl -n kernel.apparmor_restrict_unprivileged_userns 2>/dev/null || echo absent)"
echo "kernel.apparmor_restrict_unprivileged_userns = $knob"
remedy=none
if ! probe; then
  printf '%s\n' 'abi <abi/4.0>,' 'include <tunables/global>' \
    'profile bwrap /usr/bin/bwrap flags=(unconfined) {' '  userns,' \
    '  include if exists <local/bwrap>' '}' \
    | sudo tee /etc/apparmor.d/bwrap >/dev/null
  if sudo apparmor_parser -r /etc/apparmor.d/bwrap && probe; then
    remedy="AppArmor profile /etc/apparmor.d/bwrap (userns)"
  else
    sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
    remedy="sysctl kernel.apparmor_restrict_unprivileged_userns=0 (the profile was not enough)"
    probe || { echo "::error::bwrap still cannot start after both remedies"; exit 1; }
  fi
  echo "::warning::bwrap needed a remedy on this runner: $remedy"
fi
{ echo "### bwrap / AppArmor"
  echo "- bwrap: $(bwrap --version 2>&1 | head -1)"
  echo "- os: $(. /etc/os-release && echo "$PRETTY_NAME ($VERSION_ID)")"
  echo "- kernel.apparmor_restrict_unprivileged_userns: $knob"
  echo "- remedy: $remedy"; } >> "${GITHUB_STEP_SUMMARY:-/dev/stdout}"
