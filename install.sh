#!/usr/bin/env bash
# Public bootstrap: system dependencies, Homebrew and OpenCode.
set -euo pipefail
if [[ ${1:-} == --help ]]; then
  echo 'Usage: bash install.sh [--help] (interactive environment setup, then OpenCode)'
  exit 0
fi
[[ $# == 0 ]] || { echo 'Unknown argument' >&2; exit 2; }
[[ $(uname -s) == Linux ]] || { echo 'Requires Linux VPS' >&2; exit 1; }
[[ -r /dev/tty ]] || { echo 'Run from an interactive SSH terminal' >&2; exit 1; }
if [[ $EUID != 0 ]]; then
  exec sudo bash "$0" "$@"
fi
exec </dev/tty
umask 077
. /etc/os-release
case "$ID:$VERSION_ID" in
  debian:12|debian:13|ubuntu:22.04|ubuntu:24.04) ;;
  *) echo 'Supported: Debian 12/13, Ubuntu 22.04/24.04' >&2; exit 1 ;;
esac
[[ -d /run/systemd/system ]] || { echo 'Requires running systemd' >&2; exit 1; }
arch=$(dpkg --print-architecture)
[[ $arch == amd64 || $arch == arm64 ]] || { echo 'Requires amd64 or arm64' >&2; exit 1; }
apt-get update
apt-get install -y ca-certificates curl git python3 openssl restic sudo build-essential procps file
if ! command -v docker >/dev/null; then
  for pkg in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc; do
    if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed'; then
      echo "Existing $pkg: resolve Docker package conflicts manually; nothing removed." >&2
      exit 1
    fi
  done
  install -m 0755 -d /etc/apt/keyrings
  key=$(mktemp)
  curl --fail --silent --show-error --location "https://download.docker.com/linux/$ID/gpg" -o "$key"
  install -m 0644 "$key" /etc/apt/keyrings/docker.asc
  rm -f "$key"
  cat > /etc/apt/sources.list.d/docker.sources <<REPO
Types: deb
URIs: https://download.docker.com/linux/$ID
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $arch
Signed-By: /etc/apt/keyrings/docker.asc
REPO
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
fi
docker info >/dev/null
docker compose version >/dev/null
checkout=/opt/HostingService
if [[ ! -e $checkout ]]; then
  stage=$(mktemp -d /opt/hostingservice-install.XXXXXX)
  trap 'rm -rf -- "$stage"' EXIT
  git clone --depth 1 https://github.com/inkfin/HostingService.git "$stage"
  mv "$stage" "$checkout"
  trap - EXIT
else
  [[ -d $checkout/.git && -f $checkout/scripts/setup.py ]] || {
    echo "Existing $checkout is not a compatible checkout; inspect it manually." >&2; exit 1;
  }
  [[ $(git -c safe.directory="$checkout" -C "$checkout" remote get-url origin) == https://github.com/inkfin/HostingService.git ]] || {
    echo 'Unexpected repository origin; inspect manually.' >&2; exit 1;
  }
  echo 'Resuming existing checkout (no automatic update or overwrite).'
fi
cd "$checkout"
printf 'Deployment revision: '
git -c safe.directory="$checkout" rev-parse HEAD
[[ -f scripts/agent-bootstrap.sh ]] || {
  echo '现有 checkout 版本较旧。请先审阅并快进更新仓库，再重新运行安装命令。' >&2
  exit 1
}
exec bash scripts/agent-bootstrap.sh
