# Docker 数据保存与 VPS 换机

这份指南适用于本仓库当前的 Compose 配置。你可以使用[对象存储加密备份](object-storage.md)，也可以按本文手动下载到自己的电脑。

## 镜像、容器和持久化存储分别是什么

- **镜像 image**：软件及运行环境，比如 Gitea 某个版本，通常可以重新拉取。
- **容器 container**：镜像运行起来后的实例。没有挂载出去的可写层数据会随着容器删除而丢失。
- **本地目录挂载 bind mount**：把 VPS 上的目录提供给容器使用。本仓库主要使用这种方式。
- **Docker volume**：由 Docker 管理的数据目录。默认仍在这台 VPS 上，不会自动上传或跨 VPS 同步。
- **备份**：把某一时刻的数据保存成独立副本。
- **异地备份**：把副本传到 VPS 之外。只在旧 VPS 的 `backups/` 留一份，旧 VPS 到期后仍会一起丢失。

以 Gitea 为例：

```yaml
volumes:
  - ./data/gitea:/data
```

左边是 VPS 上仓库目录里的 `data/gitea`，右边是容器内 `/data`。Gitea 写入右边，文件就保存在左边。重建容器后挂载同一目录，仍能读到原来的数据。换 VPS 则要把左边目录里的内容一起搬过去。

`docker push` 上传镜像，不会上传这些挂载目录。`docker save`、`docker commit` 也不能代替挂载数据的备份。不需要为了保存数据库而重新制作一个镜像。

## 这个仓库具体保存什么

| 内容 | 存储位置 | 换机时如何处理 |
| --- | --- | --- |
| Gitea 仓库、SQLite 数据库、附件、内部配置和 SSH 信息 | `data/gitea/` | 整个目录备份，不只复制 Git 仓库 |
| TeamSpeak 数据库与文件 | `data/teamspeak/` | 整个目录备份 |
| Uptime Kuma 账号、监控配置、历史数据 | `data/uptime-kuma/` | 整个目录备份 |
| Caddy 证书及运行配置 | `data/caddy/` | 随数据备份 |
| Mihomo 订阅缓存 | `data/mihomo/` | 随数据备份 |
| 代理密码、订阅地址、Hysteria 证书和私钥、Caddyfile | `runtime/` | 必须备份，并保密 |
| 服务器地址、服务开关和端口 | `.env` | 必须备份，换机后检查地址 |
| 个人网站文件 | `website/` | 已纳入 Git，也随备份保存服务器当前版本 |
| 启动方式与对应脚本版本 | `compose.yaml`、`hosting`、`scripts/`、`config/` | 随备份保存，防止新脚本与旧配置不匹配 |
| IT-Tools | 无服务端持久化挂载 | 重新拉取镜像；浏览器端状态不属于 VPS 备份 |

以后新增服务，持久化目录优先放进 `data/服务名/`，私密配置放进 `runtime/`。当前备份脚本**不会自动寻找新增的 Docker named volume、外部绝对路径或远程数据库**，也不包含整个系统、SSH 登录配置、防火墙及其他 Compose 项目。

## 日常备份并下载到自己的电脑

以下示例假设旧 VPS 使用系统 Docker，仓库在 `/opt/HostingService`，SSH 账号是 `deploy`。用自己的实际路径和账号替换。Rootless Docker 应使用其所属用户执行命令，不要直接套用 sudo。

在旧 VPS 上执行：

```bash
cd /opt/HostingService
sudo ./hosting backup
```

脚本会停止原先运行的服务，打包数据，读取检查归档并生成 SHA-256 校验文件，最后重新启动这些服务。备份期间不能写入，所需停机时间取决于数据量。当前 SQLite 服务适合这种停服复制方式；以后引入大型 PostgreSQL/MySQL 时，应设计数据库原生备份或一致性快照。

输出类似：

```text
backups/20260908T120000000000Z.tar.gz
backups/20260908T120000000000Z.tar.gz.sha256
```

以下用 `BACKUP_NAME` 表示输出中的真实文件名，请替换示例时间。sudo 创建的备份通常只能由 root 读取，可以把**这两个归档文件**复制到当前 SSH 用户的私有目录，不要改变数据库目录的所有者：

```bash
BACKUP_NAME=20260908T120000000000Z.tar.gz
mkdir -p "$HOME/hosting-export"
chmod 700 "$HOME/hosting-export"
sudo install -m 600 -o "$(id -u)" -g "$(id -g)" \
  "backups/$BACKUP_NAME" "backups/$BACKUP_NAME.sha256" "$HOME/hosting-export/"
```

现在在**自己的电脑**执行。`scp` 是经 SSH 加密传输文件的命令；`远端账号@地址:路径` 表示远端文件，最后的 `.` 表示保存到当前本地目录：

```bash
mkdir -p ~/HostingService-backups
chmod 700 ~/HostingService-backups
cd ~/HostingService-backups
BACKUP_NAME=20260908T120000000000Z.tar.gz
scp "deploy@OLD_VPS:hosting-export/$BACKUP_NAME" .
scp "deploy@OLD_VPS:hosting-export/$BACKUP_NAME.sha256" .
chmod 600 "$BACKUP_NAME" "$BACKUP_NAME.sha256"

# macOS 检查下载是否完整
shasum -a 256 -c "$BACKUP_NAME.sha256"
# Linux 对应命令：sha256sum -c "$BACKUP_NAME.sha256"
```

校验应显示 `OK`。归档是压缩包，**没有静态加密**。SSH 只保护传输过程；电脑上请放在启用磁盘加密的私人目录。校验值用来检测损坏，不能证明来源可信。不要把归档提交到 GitHub，即使仓库是私有的。

## 真正换机：冻结旧机，然后恢复新机

1. 提前在新 VPS 安装依赖、配置 SSH，并克隆本仓库。先检查 CPU 架构、磁盘空间和 Docker 镜像下载能力。不要执行 `init` 或 `up` 创建新的服务数据。
2. 先做一次普通备份与恢复演练，确认新机器能运行。正式切换前再次生成最终备份。
3. 在旧 VPS 执行下面的迁移备份，成功后它会保持服务停止，避免旧机继续接收新数据。

```bash
cd /opt/HostingService
sudo ./hosting backup --keep-stopped
```

如果归档或校验生成失败，脚本仍会尝试重启原先运行的服务。成功后，按上一节导出并下载**这次的新文件**；不要误用演练时的旧归档。迁移期间不要启动旧容器，也不要重启旧 VPS；容器的 `unless-stopped` 策略和手工操作需要与迁移停机安排保持一致。

在自己的电脑把两份文件上传到新 VPS：

```bash
cd ~/HostingService-backups
BACKUP_NAME=20260908T120000000000Z.tar.gz
scp "$BACKUP_NAME" "$BACKUP_NAME.sha256" deploy@NEW_VPS:
```

在新 VPS 执行，下面的解压步骤只用于新建、没有业务数据的目标仓库：

```bash
cd "$HOME"
BACKUP_NAME=20260908T120000000000Z.tar.gz
chmod 600 "$BACKUP_NAME" "$BACKUP_NAME.sha256"
sha256sum -c "$BACKUP_NAME.sha256"
# 确认显示 OK 后，再执行以下命令
cd /opt/HostingService
sudo tar --numeric-owner -xzpf "$HOME/$BACKUP_NAME" -C "$PWD"
```

解压保留原来的数字 UID/GID 和权限，避免数据库变成容器无法读取的文件。不要对整个 `data/` 执行递归 `chown deploy`。若目标目录已经有业务数据，先停止服务并备份目标机；在新的空目录恢复，避免旧文件混入，不要直接覆盖线上目录。

启动前检查：

- `.env` 中的 `SERVER_HOST`、`GITEA_ROOT_URL` 是否仍是旧 IP 或旧域名。
- `runtime/Caddyfile`、域名 DNS、云安全组和防火墙是否指向新机器。
- Hysteria 客户端的 `server` 地址是否需要改变；恢复原证书和密码后无需重新生成凭据。
- 新机器是否仍是 TeamSpeak 支持的 amd64。
- 归档的 `compose.yaml` 保持原版本。先完成迁移并验证，再单独升级服务。

然后在新 VPS 执行：

```bash
cd /opt/HostingService
sudo ./hosting check && sudo ./hosting up
sudo ./hosting status
```

通过 SSH 隧道验证 Gitea 登录和实际仓库内容、一次 Git clone/push、Kuma 的监控配置、TeamSpeak 权限及代理连接。验证后完成 DNS/客户端地址切换。旧 VPS 保持停机，留到新机稳定且异地备份确认可恢复后再释放。

如果新机已接收数据，直接切回旧机将缺少这些新数据。回退前需要冻结新机并把最新数据迁回，不能简单同时启动两台服务器。

## 以后希望自动上传：使用加密备份仓库

日常可保留“VPS 当前数据 + 电脑副本 + 对象存储或 NAS 副本”。能接受丢一天的数据就每天备份；重要写入后或升级前额外备份。

自动异地备份可以使用 **restic**：它将数据加密、去重后保存到 S3 兼容对象存储、SFTP 服务器或其他支持的后端，并记录多个时间点的快照。恢复密钥必须另外保存，不能只放在即将到期的 VPS 上。

数据库仍需一致性处理：加密和上传工具不会自动把正在写入的数据库变成有效备份。可先使用本项目生成一致的归档，再上传，但每次上传压缩全量归档的去重效果不如直接备份原始文件；数据量变大后应改为数据库导出/快照与文件增量备份。

仓库已经提供 restic 上传与恢复命令，以及[服务器定时器](operations.md)。需要在实际 VPS 配置存储账号、验证恢复并启用 timer。对象存储是异地备份目的地，不要为了迁移方便就把 SQLite 数据库直接挂载在对象存储上运行。

参考：[Docker bind mount](https://docs.docker.com/engine/storage/bind-mounts/)、[Docker volume 与备份](https://docs.docker.com/engine/storage/volumes/)、[restic 仓库后端](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html)、[restic 备份说明](https://restic.readthedocs.io/en/stable/040_backup.html)。
