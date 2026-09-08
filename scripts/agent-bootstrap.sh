#!/usr/bin/env bash
# Run by the root installer, but install and launch agent tooling as a normal user.
set -euo pipefail
[[ $EUID == 0 && $(uname -s) == Linux ]] || { echo '需要 Linux root/sudo。' >&2; exit 1; }
checkout=$(cd -- "$(dirname -- "$0")/.." && pwd)
exec </dev/tty
umask 077
suggested=${SUDO_USER:-hosting}
[[ $suggested != root ]] || suggested=hosting
read -r -p "运行 Homebrew/OpenCode 的普通用户 [$suggested]: " agent_user
agent_user=${agent_user:-$suggested}
[[ $agent_user =~ ^[a-z_][a-z0-9_-]*$ && $agent_user != root ]] || { echo '无效普通用户名。' >&2; exit 1; }
if ! id "$agent_user" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$agent_user"
  echo "已创建 $agent_user；请设置供 sudo 使用的 Linux 密码（不是模型 API 密钥）。"
  passwd "$agent_user"
fi
[[ $(id -u "$agent_user") -ge 1000 ]] || { echo '请勿使用系统服务账号。' >&2; exit 1; }
agent_home=$(getent passwd "$agent_user" | cut -d: -f6)
[[ -d $agent_home && $agent_home != / && $agent_home != /root ]] || { echo '普通用户 home 不可用。' >&2; exit 1; }
# Preserve SSH policy and authorized_keys. sudo uses the normal distro policy.
usermod -aG sudo "$agent_user"
if [[ $(passwd -S "$agent_user" | awk '{print $2}') != P ]] && ! sudo -H -u "$agent_user" sudo -n true 2>/dev/null; then
  echo '此账号未设置可用密码；请设置 sudo 密码（不启用 SSH 密码登录）。'
  passwd "$agent_user"
fi
as_agent() {
  sudo -H -u "$agent_user" env -u SUDO_USER -u SUDO_UID -u SUDO_GID \
    HOME="$agent_home" USER="$agent_user" LOGNAME="$agent_user" SHELL=/bin/bash \
    PATH=/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:/usr/local/bin:/usr/bin:/bin \
    "$@"
}
cd /tmp
brew=/home/linuxbrew/.linuxbrew/bin/brew
[[ ! -L /home/linuxbrew && ! -L /home/linuxbrew/.linuxbrew ]] || { echo 'Homebrew prefix 不能是符号链接。' >&2; exit 1; }
if [[ ! -x $brew ]]; then
  # Only provision a new prefix. Never take over another user's Homebrew tree.
  if [[ ! -e /home/linuxbrew ]]; then install -d -m 0755 /home/linuxbrew; fi
  prefix=/home/linuxbrew/.linuxbrew
  if [[ ! -e $prefix ]]; then
    install -d -o "$agent_user" -g "$(id -gn "$agent_user")" -m 0755 "$prefix"
  fi
  [[ $(stat -c %U "$prefix") == "$agent_user" ]] || { echo 'Homebrew prefix 属于另一用户，请人工处理。' >&2; exit 1; }
  installer=$(mktemp)
  trap 'rm -f -- "$installer"' EXIT
  curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh -o "$installer"
  chmod 0644 "$installer"
  as_agent env NONINTERACTIVE=1 bash "$installer"
  rm -f -- "$installer"
  trap - EXIT
fi
[[ $(stat -c %U /home/linuxbrew/.linuxbrew) == "$agent_user" ]] || { echo '现有 Homebrew 属于另一用户；请使用其所有者重试。' >&2; exit 1; }
as_agent "$brew" --version
if ! as_agent "$brew" list --versions opencode >/dev/null 2>&1; then
  as_agent "$brew" install anomalyco/tap/opencode
fi
as_agent /home/linuxbrew/.linuxbrew/bin/opencode --version
# Only public checkout files and Git metadata change owner. Container UIDs and
# private runtime/data/backups remain untouched, and still require sudo.
python3 "$checkout/scripts/agent_checkout.py" "$checkout" "$agent_user"
# Append once as the owning user, preserving custom shell configuration.
as_agent bash -c '
line='\''eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv bash)"'\''
for rc in "$HOME/.profile" "$HOME/.bashrc"; do
  if ! grep -Fqx "$line" "$rc" 2>/dev/null; then printf "\n%s\n" "$line" >> "$rc"; fi
done
'
echo "Homebrew 与 OpenCode 已就绪，运行用户：$agent_user。Codex 未安装。"
echo '接下来选择模型提供商并登录；凭据留在该用户 home，不写入 HostingService。'
read -r -p '现在配置/补充 OpenCode 模型登录？[Y/n]: ' login_now
if [[ ${login_now:-y} != n && ${login_now:-y} != N ]]; then
  as_agent /home/linuxbrew/.linuxbrew/bin/opencode auth login
fi
echo '启动 OpenCode 后用 /models 选择模型；若未登录，用 /connect 完成认证。'
echo '系统命令需要 sudo 时，在自己的终端输入 Linux 密码；不把密码发给 agent。'
echo "以后进入：sudo -iu $agent_user，再 cd $checkout && opencode"
cd "$checkout"
exec sudo -H -u "$agent_user" env HOME="$agent_home" \
  PATH=/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:/usr/local/bin:/usr/bin:/bin \
  /home/linuxbrew/.linuxbrew/bin/opencode --prompt '读取本仓库 AGENTS.md 和 hostingservice-deploy skill。先检查这是新装、已有部署还是迁移，询问缺失配置，按我的选择完成部署或运维。保留已有数据、密码及备份策略；不要把环境安装完成当成服务已经验收。'
