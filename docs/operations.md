# Gitea 备份与服务器定时运维

## 可靠性边界

当前 Gitea 是 SQLite + 单 VPS 本地数据，并非高可用集群。备份流程停止服务后检查实际 `/data` 挂载、容器退出状态、SQLite 完整性、数据库记录对应的 Git 仓库及 `git fsck --full`，再归档所有数据及配置。外部数据库、非本地 storage 或未纳入的路径会报错，避免生成看似成功但不完整的备份。

归档内 `runtime/backup-manifest.json` 记录检查结果。新实例恢复后的真实登录、Issue、clone/push 仍需验收；manifest 不冒充应用恢复结果。参考 [Gitea 官方一致性要求](https://docs.gitea.com/administration/backup-and-restore/)。

## 云端与告警配置

```bash
./hosting configure-backup
# 仅全新 restic 仓库
sudo ./hosting remote init
sudo ./hosting alert-test
sudo ./hosting backup --remote
sudo ./hosting remote check
sudo ./hosting backup-health
```

`configure-backup` 隐藏输入 S3 密钥和 HTTPS 通知 webhook。通知接口需接受 `POST application/json`，字段为 `text`，返回 HTTP 2xx；请使用兼容接收端，不能把任意 URL 当通知 API。`runtime/backup-alert.json` 权限须 600。不得将 URL 中的 token 输出或提交。HTTP 2xx 只是接收端接受请求，部署时还要本人确认收到。

没有自动启用任何真实通知地址。配置好接收端且用户授权后，`alert-test` 才会发出测试通知。

## 定时器

成功完成首份异地备份、通知测试和恢复演练后，在目标 Linux VPS 执行：

```bash
sudo ./hosting schedule install --at 03:30 --timezone Asia/Shanghai
sudo ./hosting schedule status
# 立即执行一次，确认定时 service 的运行环境正确
sudo systemctl start hostingservice-backup.service
sudo journalctl -u hostingservice-backup.service -n 100 --no-pager
sudo ./hosting backup-health
```

安装 3 个系统级 timer：每天 03:30 加 0–5 分钟随机延迟全量备份；每小时检查最近异地成功时间，默认 26 小时过期；每周日 05:30 检查 restic 仓库结构。每天和每周的 timer 支持补跑错过的计划。服务使用固定部署目录和 Python 路径，不依赖 agent、交互式 shell 或电脑在线。

日常备份完成后先重启原服务再上传。数据库检查/归档失败会返回非零并尝试重启服务；云端上传失败保留本地归档，发送告警并记录错误。健康检查读 `runtime/backup-status.json`，以真实上传成功时间判断。

```bash
# 无系统修改，预览 units
./hosting schedule render
# 迁移前停用旧机定时器，避免切换期间再次备份旧数据
sudo ./hosting schedule disable
sudo ./hosting backup --remote --keep-stopped
```

当前支持 Linux systemd + rootful Docker；系统一次只安装这一套名称的 timer。不同部署目录共用同名服务会被拒绝覆盖。rootless Docker、其他 init 系统需额外适配。定时器异常在 systemd/journal 中可见；如果整个 VPS 掉线，机内 timer 无法发告警，建议另有外部存活监控。

## 历史与容量

当前不会自动删除本地归档或云端快照。定期检查 `du -sh backups` 和对象存储用量；备份频率与空间预算在部署时需说明。本版先保证可恢复与错误可见，保留清理须后续明确策略后用 restic forget/prune 实现，不能通过 bucket 生命周期随意删除内部对象。
