# 配置收集表

| 项目 | 输入/默认 | 验证与去向 |
|---|---|---|
| 目标 | SSH host、目录、新装/迁移 | 先验证目标身份与现有数据 |
| 服务 | website,gitea,uptime-kuma,it-tools；按需 teamspeak | `.env` 的 COMPOSE_PROFILES |
| 网站 | IP HTTP 或域名 HTTPS | DNS 与 80/443，runtime/Caddyfile |
| Gitea | 管理员名/email、私有隧道或公网域名、SSH 公钥 | runtime/gitea-admin.txt、Gitea 设置；必须真实 clone/push |
| 代理 | none/china/overseas | china 隐藏订阅；overseas 导出客户端与指纹 |
| TeamSpeak | amd64、许可、服务器密码 | 用户接受许可，领取管理权限密钥 |
| Kuma | 管理员、至少一个监控目标 | 经隧道或浏览器配置并检查 |
| 异地备份 | S3 endpoint、bucket/前缀、region、密钥 | runtime/restic.json；真实 init/backup/check |
| 解密密码 | 新库生成，旧库沿用 | runtime/restic-password，另存密码管理器 |
| 通知 | HTTPS webhook，接收 JSON text | runtime/backup-alert.json；测试并确认送达 |
| 定时 | 03:30 Asia/Shanghai，可调整 | systemd timers；显示下一次执行 |
| 保留 | 默认不自动删除；明确磁盘/云存储预算 | 未实现自动清理，不把“无限保留”说成已管理容量 |
| 验收 | 业务连接、云端恢复、timer 执行、告警 | runtime/deployment-report.md |

保密字段不通过普通聊天收集。optional 项可以由用户明确不启用；必需项未完成则部署保持未完成。
