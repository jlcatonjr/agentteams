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

## 0b. Provisioning a fresh VM the agent can SSH into (recommended, headless CLI)

The most reliable path is to **generate the VM unattended** with SSH **key auth** and the
§3 sshd relaxation baked in, so it boots ready and no password is ever typed. Needs an
Ubuntu Server ISO matching the host arch (`uname -m`; Apple Silicon = `arm64`). Skeleton:

```bash
VM=LinuxSandbox
ISO="$HOME/Downloads/ubuntu-26.04-live-server-arm64.iso"
VMDIR="$HOME/VirtualBox VMs/$VM"
VMPW=changeme            # console/backup login value; record it in the gitignored .env
VBoxManage createvm --name "$VM" --ostype Ubuntu_arm64 --register
VBoxManage modifyvm "$VM" --memory 4096 --cpus 2 --firmware efi --nic1 nat
VBoxManage modifyvm "$VM" --natpf1 "ssh,tcp,127.0.0.1,2223,,22"   # pick a free host port
VBoxManage createmedium disk --filename "$VMDIR/$VM.vdi" --size 20000
VBoxManage storagectl "$VM" --name SATA --add sata --controller IntelAHCI
VBoxManage storageattach "$VM" --storagectl SATA --port 0 --device 0 --type hdd --medium "$VMDIR/$VM.vdi"

# Provisioning script that runs as root in the target after install; base64 it to dodge
# all quoting. Installs openssh + bubblewrap, adds the host public key, relaxes sshd.
# Home path uses a $U variable so no literal user home path is hard-coded.
PUB="$(cat ~/.ssh/id_rsa.pub)"
B64=$(printf '%s\n' \
  'U=vboxuser; D="/home/$U/.ssh"' \
  'apt-get update -qq || true' \
  'apt-get install -y openssh-server bubblewrap || true' \
  'install -d -m700 -o $U -g $U "$D"' \
  "printf '%s\n' '$PUB' > \"\$D/authorized_keys\"" \
  'chmod 600 "$D/authorized_keys"; chown $U:$U "$D/authorized_keys"' \
  "printf 'UseDNS no\nMaxStartups 100:30:200\n' > /etc/ssh/sshd_config.d/99-vmtest.conf" \
  'systemctl enable ssh || true' | base64 | tr -d '\n')

VBoxManage unattended install "$VM" --iso="$ISO" \
  --user=vboxuser --user-password=$VMPW --full-user-name="VBox Sandbox" \
  --install-additions --locale=en_US --country=US --time-zone=UTC \
  --post-install-command="echo $B64 | base64 -d | bash" \
  --start-vm=headless
```

Then poll for readiness and connect **key-based** (no password):
`ssh -p 2223 -i ~/.ssh/id_rsa vboxuser@127.0.0.1`. Install takes ~15–20 min; the VM
auto-reboots into the installed system. Key auth + the baked-in `UseDNS no`/`MaxStartups`
sidestep §3's banner-stall and rate-limit entirely — no interactive-password dance.

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

**Durable fix for automated testing — run INSIDE the VM console (not the host).** Write to
the **server** drop-in dir `/etc/ssh/sshd_config.d/` (note the `d` in `sshd`) — **not**
the client dir `/etc/ssh/ssh_config.d/` (which holds `20-systemd-ssh-proxy.conf`; a common
mix-up). `PerSourcePenalties`/`PerSourceMaxStartups` exist only in **OpenSSH 9.8+**
(Ubuntu 24.10+); on 24.04 (OpenSSH 9.6) they are invalid and `sshd` will refuse to
restart — so add them **conditionally** and validate with `sshd -t` before restarting:

```
SSHD=$(command -v sshd || echo /usr/sbin/sshd)   # sshd is in /usr/sbin, not a user's PATH
{
  echo 'UseDNS no'
  echo 'MaxStartups 100:30:200'
  if sudo "$SSHD" -T 2>/dev/null | grep -qi '^persourcepenalties'; then
    echo 'PerSourcePenalties no'
    echo 'PerSourceMaxStartups none'
  fi
} | sudo tee /etc/ssh/sshd_config.d/99-vmtest.conf >/dev/null
sudo "$SSHD" -t && sudo systemctl restart ssh && echo APPLIED-OK || echo SSHD-CONFIG-ERROR
```
(The service unit is `ssh` on Debian/Ubuntu; if `restart ssh` reports no such unit, use
`sudo systemctl restart sshd`.)

`UseDNS no` kills the banner stall; the high `MaxStartups` and (where supported) the
per-source directives stop sshd dropping automated repeated connections. `sshd -t`
guarantees a bad directive cannot brick sshd. Treat this VM as test-only — these settings
loosen sshd's abuse protections deliberately. If the drop-in dir is not `Include`d by the
main config (`grep -i '^include' /etc/ssh/sshd_config` shows nothing), append the same
lines to `/etc/ssh/sshd_config` instead.

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
