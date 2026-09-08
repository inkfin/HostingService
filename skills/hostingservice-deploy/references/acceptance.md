# 完成交付的证据

- 记录目标系统、架构、磁盘、Docker/Compose 和部署 commit。确认上游镜像可拉取。
- 已选服务均有业务验证：网站实际请求、Gitea 登录与 SSH/HTTP clone/push、TeamSpeak 连接和权限、代理真实出站、Kuma 账号及一个监控目标。未选择服务记为不适用。
- `backup --remote` 成功；保存本次快照 ID，不能只引用历史快照或本地 tar 的存在。
- Gitea 备份 manifest 有 SQLite integrity_check 与 git fsck 结果。用云端取回的备份，在隔离环境启动相同版本 Gitea；核对用户、仓库、Issue/附件/LFS（如启用），实际 clone/push。上游镜像或数据库格式变化后重新演练。
- `remote check` 检查仓库结构；`--read-data` 为读取所有备份块，会产生下载流量。二者都不能代替应用恢复演练。
- 通知测试被用户确认收到。HTTP 2xx 只说明 webhook 接受请求，不证明人看到了消息。
- systemd timer enabled/active，记录下一次时间，实际执行一次 backup service 并检查 journal 与新快照。
- `backup-health` 检查最近异地成功时间，默认 26 小时过期；失败返回非零。独立外部监控仍有价值：整台 VPS 关机时，机内 timer 无法自行报警。
- 恢复测试资源使用独立目录、独立 compose project、仅 loopback 端口。清理仅限本次创建的测试资源。

禁止将“脚本测试通过”“容器 running”“SHA256 匹配”单独当作完整部署或恢复验收。
