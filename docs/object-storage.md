# 对象存储异地备份

本项目使用 restic 加密上传到 S3 兼容对象存储。这里的 S3 是存储接口标准，不要求服务器或存储一定属于 AWS。

服务仍然读写 VPS 的 `data/`。执行备份时，脚本停服生成一致的归档，恢复原先运行的服务，然后把归档及校验文件交给 restic 加密上传。换机模式会保持旧服务停止。

当前实现每次生成全量 `.tar.gz`，适合数据量不大、允许短暂停服的个人服务。restic 虽支持去重，但压缩归档每次变化时去重收益有限。不要据此估算“只上传变化的数据库页面”；大相册或大型数据库需要另外设计增量备份。

## 首次准备

需要一个私有 bucket、对应的 S3 HTTPS endpoint、访问密钥和地域。bucket 是对象存储中的文件容器，不是 Docker 容器。为这个部署选择独立前缀，例如 `hostingservice`；不同独立部署使用不同前缀，迁移同一部署时沿用原前缀。

给备份账号授予该 bucket/前缀所需的列举、读、写权限，以及 restic 清理锁文件需要的删除权限，不要开放公共访问。不在云厂商控制台配置“定期删除任意对象”的生命周期规则，这会损坏 restic 仓库。厂商若支持 S3 兼容接口但未实际验证，应先做下面的上传/恢复演练。

在 VPS 安装 restic。Debian/Ubuntu 可用发行版包：

```bash
sudo apt-get update
sudo apt-get install -y restic
restic version
```

其他系统见 [restic 官方安装说明](https://restic.readthedocs.io/en/stable/020_installation.html)。

在本仓库准备私有配置：

```bash
mkdir -p runtime
chmod 700 runtime
# 仅首次执行；已有配置不要覆盖
cp -n config/restic.example.json runtime/restic.json
chmod 600 runtime/restic.json
```

编辑 `runtime/restic.json`，替换所有 `REPLACE_...`。密钥写在这个被 Git 忽略的文件里，不发到聊天、不写入仓库。`repository` 格式为 `s3:https://端点/bucket/前缀`；endpoint 应使用厂商文档中支持的 S3 API 地址。`AWS_DEFAULT_REGION` 按该厂商要求填写；若不需要此项，可以删除这一行并保持 JSON 合法。

只在第一次创建全新备份仓库时生成加密密码：

```bash
# noclobber 防止覆盖已有密码；如果文件已存在，命令应报错而不是重置
(umask 077; set -o noclobber; openssl rand -base64 48 > runtime/restic-password)
```

把 `restic-password` 的内容存入自己的密码管理器，并另存 repository 地址及获取存储访问密钥的方法。**原 VPS 丢失后，只有云存储密钥而没有 restic 密码，仍然不能解密备份。** 密码文件也会随 runtime 被加密备份，但那一份不能用于首次解锁自身。

初始化 restic 仓库：

```bash
sudo ./hosting remote init
sudo ./hosting backup --remote
sudo ./hosting remote snapshots
```

`remote init` 只创建新的 restic 仓库，不会自动创建云账号或购买存储。已有备份仓库直接用原密码连接，不要重新生成密码或重复初始化。

## 日常使用

```bash
# 生成本地一致性备份、恢复服务，然后加密上传
sudo ./hosting backup --remote

# 查看历史备份的快照 ID、时间和来源主机
sudo ./hosting remote snapshots

# 检查仓库结构，不等于读取所有备份数据
sudo ./hosting remote check

# 完整读取数据校验；会产生云端请求及下载流量
sudo ./hosting remote check --read-data
```

上传前会检查凭据文件权限、restic 密码和仓库访问，常见配置错误会在停服之前报出。若中途上传失败，本地归档保留；日常备份的服务已恢复运行。重试只上传该归档，不再停服：

```bash
sudo ./hosting remote upload --archive backups/实际时间.tar.gz
```

上传成功不等于应用恢复验证通过。至少做一次下面的下载、校验、解压和实际启动演练。

本版本不会自动清理本地归档或云端快照。留意两处存储空间。将来配置保留策略时，用 restic 的 `forget` / `prune` 管理云端历史，不要直接删 bucket 中的对象。配置目的地、告警并验证恢复之后，按[定时备份运维](operations.md)安装服务器 timer。

## 换 VPS 时从对象存储恢复

旧 VPS 生成最后一份备份，并保持停机：

```bash
sudo ./hosting backup --remote --keep-stopped
sudo ./hosting remote snapshots
```

如果已启用定时器，先运行 `sudo ./hosting schedule disable`，再生成最终备份。记下**此次成功上传**的快照 ID。上传失败时旧服务仍停止，先重试上传，不要释放旧 VPS。需要取消迁移时按命令输出恢复旧服务。

新 VPS 安装 Docker、Python、OpenSSL、restic 并克隆本仓库。此时不要执行 `./hosting init` 或 `./hosting up`。从密码管理器配置相同的 `runtime/restic.json` 和**原来的** `runtime/restic-password`，权限设为 600；不要执行 `remote init`。

```bash
sudo ./hosting remote snapshots
sudo ./hosting remote restore --snapshot 这里替换成快照ID --target /opt/hosting-restore
```

脚本要求明确的快照 ID；目标目录必须为空或不存在。恢复只下载归档，不会覆盖或启动服务。上传使用相对路径，下载的归档及校验文件位于目标目录中。

在新 VPS 上验证归档：

```bash
sudo -i
cd /opt/hosting-restore
sha256sum -c 实际时间.tar.gz.sha256
# 确认 OK 后，进入预先克隆的仓库
cd /opt/HostingService
tar --numeric-owner -xzpf /opt/hosting-restore/实际时间.tar.gz -C "$PWD"
```

解压会覆盖仓库内的 `.env`、`runtime/` 和归档包含的代码，因此应在新建、没有业务数据的仓库中操作。预先填写的新机备份凭据也会被归档中的旧副本覆盖；若已经轮换存储密钥，恢复后需要重新填写新密钥。

按[迁移指南](storage-and-migration.md#真正换机冻结旧机然后恢复新机)检查地址、DNS、UID/GID、架构及端口后启动。验证业务数据后再切换访问；旧 VPS 保持停机，直到新机和异地备份均验证完成。

## 已完成与尚未完成

仓库已提供 S3 配置模板、加密上传命令、上传失败重试、快照查询、检查及下载恢复。本地可用 restic 的本地文件后端测试加密备份和恢复流程。

云存储的 bucket、endpoint、访问密钥、网络连通性和权限仍需针对实际厂商验证。仓库提供定时器安装命令，但仅克隆仓库不会安装或启用。当前没有创建用户云资源、上传用户数据或自动删除任何备份。

参考：[restic S3 后端](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html#amazon-s3)、[备份](https://restic.readthedocs.io/en/stable/040_backup.html)、[检查仓库](https://restic.readthedocs.io/en/stable/045_working_with_repos.html)、[恢复](https://restic.readthedocs.io/en/stable/050_restore.html)。
