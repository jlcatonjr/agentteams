# Virtual-Machine Testing Runbook

> Operational guide for driving a real Linux VM (VirtualBox) from this repo when a
> test needs a **genuine kernel** and a Docker container will not do — e.g. verifying
> Claude Code's **bubblewrap** sandbox backend (`denyRead`/`allowWrite` enforcement) for
> the workspace-privilege-scoping (P1/P2/P3) work. A container is a namespace sandbox
> itself and confounds a bubblewrap-under-test; a VM gives an unshared kernel.
>
> This lives in the repo's **instance infrastructure** (`references/`), not in the
> `agentteams/` Python module — it governs how *this repository's* operators test, it is
> not a feature of the generated-team tool.

## 0. TL;DR — the reliable path

1. Login details live in a **gitignored** `.env` at the repo root (keys
   `testLinux_USERNAME`, `testLinux_PASSWORD`). Confirm it is ignored before doing
   anything: `git check-ignore .env`.
2. Start the VM headless and add a NAT ssh forward:
   `VBoxManage startvm "<VM>" --type headless` then
   `VBoxManage controlvm "<VM>" natpf1 "ssh,tcp,127.0.0.1,2222,,22"`.
3. **Clear the stale host key** (a recreated VM reuses `127.0.0.1:2222` with a new key):
   `ssh-keygen -R "[127.0.0.1]:2222"`.
4. Connect: `ssh -p 2222 <user>@127.0.0.1`. The VirtualBox default user is `vboxuser`.
5. If sshd stalls or drops the connection, see §3 — the VM's `sshd_config` almost
   certainly has `UseDNS yes` and aggressive per-source penalties that fight automated
   access over NAT. The durable fix is §3.1 (one console edit).

## 1. VM lifecycle (VBoxManage)

| Task | Command |
|---|---|
| list VMs | `VBoxManage list vms` |
| list running | `VBoxManage list runningvms` |
| start headless | `VBoxManage startvm "<VM>" --type headless` |
| power off | `VBoxManage controlvm "<VM>" poweroff` |
| VM state / OS | `VBoxManage showvminfo "<VM>" --machinereadable \| grep -E "VMState=\|ostype="` |
| console screenshot | `VBoxManage controlvm "<VM>" screenshotpng out.png` |

Note: a headless **server** VM often reports console resolution `0x0` — that is normal
(no framebuffer), **not** proof of a stuck boot. Diagnose boot state by whether sshd
answers, not by the screenshot.

## 2. Networking & access

- These VMs use **NAT** (`nic1=nat`). NAT has no routable guest IP, so reach sshd via a
  host port-forward: `VBoxManage controlvm "<VM>" natpf1 "ssh,tcp,127.0.0.1,2222,,22"`
  (forwards host `127.0.0.1:2222` → guest `22`). Re-add it after a VM recreation; it does
  **not** survive deleting/recreating the VM.
- Confirm reachability: `nc -z -w3 127.0.0.1 2222`.
- **Login details:** never hard-code them. Read from the gitignored `.env`. The VirtualBox
  unattended-install default user is `vboxuser`. If the recorded user does not connect, the
  value in `.env` may be stale — re-confirm against the VM before retrying (repeated failed
  logins make §3.2 worse).
- **NEVER commit `.env`.** It is in `.gitignore`; keep it there. Do not echo its values
  into committed files or transcripts beyond what a live debug requires.

## 3. The two failure modes that waste the most time

Both were hit repeatedly across two VMs in this repo. They are **VM-side sshd config**
problems, not client bugs.

### 3.1 Banner stall — empty SSH banner / `kex_exchange_identification` hang

**Symptom:** the TCP port is open (`nc` succeeds) but `ssh` hangs with no version banner,
or times out "during banner exchange", or reads an empty banner. **Cause:** `sshd` doing a
reverse-DNS (`UseDNS yes`) lookup on the NAT client `10.0.2.2`, which has no reverse
record and stalls ~30–60 s **per connection**. **Durable fix — run these commands
INSIDE the VM (via its VirtualBox/RDP console, logged in as the VM user), NOT on the
macOS host.** Editing the host's `/etc/ssh/sshd_config` is wrong and affects the Mac's
own SSH. Use a drop-in file, not `sed` (macOS ships BSD `sed`, which rejects the GNU
`-i`/`\?` syntax with `bad flag in substitute command`; the drop-in is portable and
idempotent and Ubuntu's `sshd_config` already `Include`s `sshd_config.d/*.conf`):

```
echo 'UseDNS no' | sudo tee /etc/ssh/sshd_config.d/99-vmtest.conf
sudo systemctl restart ssh
```

**Client-side stopgap** (survive one stall without the fix): a long timeout, e.g.
`ssh -p 2222 -o ConnectTimeout=95 ...`, so the reverse-lookup times out and the banner
finally arrives. This is slow and unreliable; prefer the console fix.

### 3.2 Connection reset / dropped — per-source rate-limit under NAT

**Symptom:** `Connection reset by peer` during kex, or `Connection closed`, after a few
attempts; a long cooldown does not obviously help because each new attempt re-arms it.
**Cause:** OpenSSH `PerSourcePenalties` / `MaxStartups` (Ubuntu 24.04+ defaults). Under
NAT **every** host connection appears to come from the single guest-side address, so a
handful of failed or half-open attempts trips a per-source block that escalates with each
further attempt — you will see `nc` succeed (TCP accepted) while `ssh` fails at the banner
with `Connection to UNKNOWN port -1: Socket is not connected`. A human logging in once
does not trip it; automated repeated connections do. **Recovery:** stop attempting for
several minutes (or reboot / `sudo systemctl restart ssh`).

**Durable fix for automated testing — run these INSIDE the VM console (not the host).**
This is essential if a script (not just a human) must connect; combine it with the
`UseDNS no` line from §3.1 into one drop-in:

```
sudo tee /etc/ssh/sshd_config.d/99-vmtest.conf >/dev/null <<'EOF'
UseDNS no
MaxStartups 100:30:200
PerSourceMaxStartups none
PerSourcePenalties no
EOF
sudo systemctl restart ssh
```

`PerSourcePenalties no` disables the escalating per-source block; `PerSourceMaxStartups
none` removes the per-source connection cap; the high `MaxStartups` tolerates concurrent
unauthenticated connections. After this, automated SSH from the host connects reliably.
Treat this VM as test-only — these settings loosen sshd's abuse protections deliberately.

### 3.3 Why `VBoxManage guestcontrol` was not a workaround

`guestcontrol ... run` bypasses sshd entirely, which is attractive when sshd misbehaves —
but it needs the full **Guest Additions guest-control (VBoxService) exec** component. On
the minimal / ARM Ubuntu images used here it reports *"The guest execution service is not
ready (yet)"* indefinitely (the additions are partially present — guest properties update —
but the control service is not installed/running). Treat guestcontrol as unavailable
unless `virtualbox-guest-utils` (with the control service) is confirmed in the guest.

## 4. Host-key hygiene on VM recreation

Deleting and recreating a VM that reuses `127.0.0.1:2222` gives sshd a **new** host key.
The client then refuses the connection or disables the interactive login method
("possible man-in-the-middle", "Offending key in known_hosts:N"). Always clear the old
entry first:

```
ssh-keygen -R "[127.0.0.1]:2222"
```

`StrictHostKeyChecking=no` alone does not always suppress the interactive-login lockout,
so clear the entry explicitly.

## 5. Reference: the bubblewrap read-deny spike

Once a shell is available, the Linux mirror of the macOS Seatbelt spike is ~5 commands.
It verifies the **mechanism** (bwrap can deny a read while the toolchain keeps working);
it does **not** verify Claude Code's `denyRead`→bwrap translation (that needs Claude Code
in the guest). Keep that distinction in any doc claim (see the privilege-scoping G-F gap).

```bash
sudo apt-get update && sudo apt-get install -y bubblewrap
mkdir -p /tmp/ws /tmp/protected && echo canary > /tmp/protected/marker
# allow the toolchain + workspace read; deny the protected dir; confine writes to /tmp/ws
bwrap --ro-bind / / --bind /tmp/ws /tmp/ws --tmpfs /tmp/protected \
      --dev /dev --proc /proc \
      /bin/sh -c 'python3 -c "print(2+2)"; cat /tmp/protected/marker 2>&1 || echo DENIED-OK'
```

A `DENIED-OK` line with a working `python3` demonstrates read-exclusion while the toolchain
runs. (Bind-mount semantics differ from Claude Code's own bwrap arg generation; this proves
the kernel mechanism, not the product's translation.)

## 6. Security notes

- `.env` is gitignored and must stay so; VM login details never enter a commit.
- If VRDE (RDP console) is used, bind it to `127.0.0.1` only (VirtualBox VRDE has no
  authentication): `VBoxManage controlvm "<VM>" vrdeproperty "TCP/Address=127.0.0.1"`.
- Treat any VM used for privilege/sandbox testing as untrusted for outbound network unless
  deliberately configured otherwise.

## 7. Known-good session recipe (copy/paste skeleton)

```bash
set -a; . ./.env; set +a            # load gitignored login details
VM="LinuxTestMachine"
VBoxManage list runningvms | grep -q "$VM" || VBoxManage startvm "$VM" --type headless
VBoxManage showvminfo "$VM" --machinereadable | grep -qi "Forwarding.*2222" \
  || VBoxManage controlvm "$VM" natpf1 "ssh,tcp,127.0.0.1,2222,,22"
ssh-keygen -R "[127.0.0.1]:2222" >/dev/null 2>&1
# one attempt; if it stalls/drops, apply the section 3.1 fix in the VM console, then retry:
ssh -p 2222 -o ConnectTimeout=95 "$testLinux_USERNAME"@127.0.0.1 'uname -a; which bwrap'
```
