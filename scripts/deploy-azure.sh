#!/usr/bin/env bash
# Run the whole Tier0 app on an Azure virtual machine, from your Mac (or any machine with `az`).
#
#   scripts/deploy-azure.sh            create the VM (first time) and start the app on it
#   scripts/deploy-azure.sh update     copy the current code to the VM and restart the app
#   scripts/deploy-azure.sh status     show the VM and the containers
#   scripts/deploy-azure.sh logs       follow the app's log
#   scripts/deploy-azure.sh ssh        open a shell on the VM
#   scripts/deploy-azure.sh destroy    delete EVERYTHING it created (the whole resource group)
#
# Options: --yes (no questions)  --dry-run (print the Azure commands, create nothing)
#          --host user@address   use an existing Linux VM instead of creating one
# Settings (environment variables): RG (default rg-tier0-vm), LOCATION (eastus), VM_NAME
#   (tier0-vm), VM_SIZE (Standard_B1ms: 1 vCPU, 2 GB), ALLOW_IP (the one address allowed to reach
#   the VM; default: this machine's public address).
#
# What it creates: a resource group, an Ubuntu 24.04 VM with Docker, a public IP, and a firewall
# that lets ONLY your address reach SSH (22) and the app (80). The app runs exactly as on your Mac
# (scripts/run-local.sh), with the same .env (your OpenAI key is copied to the VM, mode 600).
# It serves plain HTTP, so it is a demo setup: do not use real passwords. A running VM costs money
# (the default size is Azure's smallest B-series VM that can hold the app; check current prices); `destroy` stops it all.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
RG="${RG:-rg-tier0-vm}"; LOCATION="${LOCATION:-eastus}"; VM_NAME="${VM_NAME:-tier0-vm}"
VM_SIZE="${VM_SIZE:-Standard_B1ms}"; ADMIN="tier0admin"; REMOTE_DIR="tier0"

COMMAND="up"; ASSUME_YES=0; DRY_RUN=0; HOST=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    up|update|status|logs|ssh|destroy) COMMAND="$1" ;;
    --yes|-y) ASSUME_YES=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --host) HOST="$2"; shift ;;
    -h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '%s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
ask() { [[ "$ASSUME_YES" == 1 ]] && return 0; [[ -t 0 ]] || die "$1 (run again with --yes to agree)"
        local r; read -r -p "$1 [y/N] " r; [[ "$r" =~ ^[Yy]$ ]]; }
az_do() { # run (or just show, with --dry-run) an Azure command
  if [[ "$DRY_RUN" == 1 ]]; then printf '[dry run] az %s\n' "$*"; else az "$@"; fi
}
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o ServerAliveInterval=30)
remote() { ssh "${SSH_OPTS[@]}" "$TARGET" "$@"; }

check_tools() {
  command -v ssh >/dev/null || die "ssh is required."
  command -v tar >/dev/null || die "tar is required."
  if [[ -z "$HOST" ]]; then
    command -v az >/dev/null || die "Install the Azure CLI: https://learn.microsoft.com/cli/azure/install-azure-cli (Mac: brew install azure-cli)"
    [[ "$DRY_RUN" == 1 ]] || az account show >/dev/null 2>&1 || die "Sign in first: az login"
  fi
}

vm_ip() { az vm show -d -g "$RG" -n "$VM_NAME" --query publicIps -o tsv 2>/dev/null || true; }

resolve_target() {
  if [[ -n "$HOST" ]]; then TARGET="$HOST"; return; fi
  local ip; ip="$(vm_ip)"; [[ -n "$ip" ]] || die "The VM $VM_NAME does not exist yet. Run: scripts/deploy-azure.sh"
  TARGET="$ADMIN@$ip"
}

my_ip() {
  if [[ -n "${ALLOW_IP:-}" ]]; then printf '%s' "$ALLOW_IP"; return; fi
  curl -fsS --max-time 10 https://api.ipify.org || die "Could not find this machine's public address. Set ALLOW_IP=<address>."
}

CLOUD_INIT='#cloud-config
package_update: true
swap:
  filename: /swapfile
  size: 4294967296
packages: [docker.io, docker-compose-v2, rsync]
runcmd:
  - [ systemctl, enable, --now, docker ]
  - [ usermod, -aG, docker, tier0admin ]
  - [ touch, /var/lib/tier0-ready ]
'

create_vm() {
  local ip_allowed; ip_allowed="$([[ "$DRY_RUN" == 1 ]] && echo "<your-ip>" || my_ip)"
  say "Subscription:  $(az account show --query name -o tsv 2>/dev/null || echo '<current>')"
  say "Resource group: $RG ($LOCATION)   VM: $VM_NAME ($VM_SIZE, Ubuntu 24.04)"
  say "Firewall:       only $ip_allowed may reach SSH (22) and the app (80)"
  ask "Create these Azure resources (this starts billing)?" || exit 1
  az_do group create -n "$RG" -l "$LOCATION" -o none
  local nsg="${VM_NAME}-nsg"
  az_do network nsg create -g "$RG" -n "$nsg" -l "$LOCATION" -o none
  local cloud_file; cloud_file="$(mktemp)"; printf '%s' "$CLOUD_INIT" > "$cloud_file"
  az_do vm create -g "$RG" -n "$VM_NAME" -l "$LOCATION" --size "$VM_SIZE" \
    --image Canonical:ubuntu-24_04-lts:server:latest --admin-username "$ADMIN" \
    --generate-ssh-keys --public-ip-sku Standard --nsg "$nsg" --nsg-rule NONE \
    --os-disk-size-gb 64 --custom-data "$cloud_file" -o none
  rm -f "$cloud_file"
  local prio=1000 port
  for port in 22 80; do
    az_do network nsg rule create -g "$RG" --nsg-name "$nsg" -n "allow-$port-from-owner" \
      --priority "$prio" --access Allow --protocol Tcp --direction Inbound \
      --source-address-prefixes "$ip_allowed" --destination-port-ranges "$port" -o none
    prio=$((prio + 10))
  done
}

wait_for_vm() {
  say "Waiting for the VM to finish installing Docker (a few minutes)..."
  local i
  for i in $(seq 1 60); do
    if remote 'test -f /var/lib/tier0-ready' 2>/dev/null; then return 0; fi
    sleep 10
  done
  die "The VM did not become ready in time. Check: scripts/deploy-azure.sh ssh"
}

send_code() {
  [[ -f .env ]] || die "No .env here. Run scripts/run-local.sh once (it asks for your OpenAI key), then try again."
  say "Copying the code to the VM..."
  local excludes=() line
  while IFS= read -r line; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    excludes+=(--exclude="${line%/}")
  done < scripts/.packignore
  # The prebuilt index (.data/chroma) is included, so the VM does not have to build it.
  COPYFILE_DISABLE=1 tar czf - "${excludes[@]}" . | remote "mkdir -p $REMOTE_DIR && tar xzf - -C $REMOTE_DIR"
  say "Copying .env (your keys) to the VM..."
  remote "umask 077 && cat > $REMOTE_DIR/.env" < .env
}

start_app() {
  say "Starting the app on the VM (the first time builds it and takes several minutes)..."
  remote "cd $REMOTE_DIR && chmod +x scripts/run-local.sh && scripts/run-local.sh up --yes --bind 0.0.0.0 --port 80 --random-demo-passwords"
}

case "$COMMAND" in
  up)
    check_tools
    if [[ -n "$HOST" ]]; then
      TARGET="$HOST"; say "Using the existing host $TARGET (Docker and Docker Compose must be installed there)."
    else
      if [[ "$DRY_RUN" == 1 || -z "$(vm_ip)" ]]; then create_vm; else say "The VM $VM_NAME already exists; updating it."; fi
      if [[ "$DRY_RUN" == 1 ]]; then say "[dry run] then: wait for Docker, copy the code and .env, run scripts/run-local.sh up on the VM"; exit 0; fi
      resolve_target; wait_for_vm
    fi
    send_code; start_app
    ip="${TARGET#*@}"
    say ""; say "Tier0 is running on Azure:  http://$ip"
    say "Only $(my_ip 2>/dev/null || echo 'your address') can open it. To let someone else in, add their address to the VM's network security group."
    say "Remove it all:  scripts/deploy-azure.sh destroy"
    ;;
  update) check_tools; resolve_target; send_code; start_app ;;
  status) check_tools; resolve_target; remote "cd $REMOTE_DIR && scripts/run-local.sh status" ;;
  logs) check_tools; resolve_target; remote "cd $REMOTE_DIR && scripts/run-local.sh logs" ;;
  ssh) check_tools; resolve_target; exec ssh "${SSH_OPTS[@]}" "$TARGET" ;;
  destroy)
    check_tools; [[ -z "$HOST" ]] || die "destroy only removes what this script created in Azure."
    az group exists -n "$RG" | grep -q true || die "Resource group $RG does not exist; nothing to delete."
    ask "Delete resource group $RG and EVERYTHING in it (the VM, its disk and IP)?" || exit 1
    az_do group delete -n "$RG" --yes --no-wait
    say "Deletion started; it finishes in a few minutes."
    ;;
esac
