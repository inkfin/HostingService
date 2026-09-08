---
name: hostingservice-deploy
description: Deploy or migrate the user's HostingService Docker stack on a Linux VPS, guiding missing configuration and verifying services, Gitea recovery, object-storage backups and server timers before handoff. Use for HostingService setup, backup setup, or VPS migration.
---

# HostingService deployment walkthrough

完成用户指定 VPS 的部署和验收。用户要求部署已授权范围内的服务后，持续执行可自动完成的配置；不要仅交付命令清单，也不要在每个可逆步骤重复询问许可。保留用户选择的服务、仓库可见性、域名、备份频率和风险取舍。

## 获取代码与目标

安装 skill 只提供操作说明，不代表服务已安装。先确认目标 SSH host、部署目录和新装/已有/迁移状态；不把当前电脑当作目标 VPS。用当前环境已有的 SSH/remote 工具，检查 Linux、CPU、磁盘、现有容器、占用端口、Docker/Compose/Python/OpenSSL/Git/restic/systemd。

从 `https://github.com/inkfin/HostingService.git` 克隆部署仓库。仓库已按用户要求公开，下载无需认证。白板受支持 VPS 可先运行 README 的 install.sh，再用 ./hosting setup 配置；不必在服务器安装 agent。已有 checkout 先检查改动和版本，不覆盖 `.env`、`runtime/`、`data/`，不运行清库命令。安装后的 skill 目录可能独立于源码，不能用 `../../` 猜测部署代码位置。

读取 checkout 的 `docs/deployment.md`、`docs/operations.md`；备份设置再读 `docs/object-storage.md`，迁移再读 `docs/storage-and-migration.md`。本 skill 的 [配置清单](references/configuration.md) 和 [验收标准](references/acceptance.md) 可用于未克隆成功时说明缺项；执行命令以实际 checkout 的 `./hosting --help` 为准。

## 询问与凭据

首次显示简短阶段进度，并合并询问尚缺的非敏感信息：目标/服务、域名与访问方式、代理模式、对象存储、备份时间/时区/保留需求、通知渠道。已有答案不重复问；有合理默认值时说明默认并继续独立工作。

如果 agent 环境提供用户输入表单或提示框，优先使用；无此工具则用简短文字或终端输入。skill 无法保证所有 agent 都能弹 GUI，也不绕过执行环境的权限审核。

密钥不能放在普通聊天问题、工具输出、命令历史或 Git。通过支持保密输入的界面或用户自己的终端执行 `./hosting configure-backup`；若没有保密输入能力，提示用户在目标机填写被忽略的配置文件。代理订阅同样用隐藏输入。密码管理器解锁、云账号登录、付款、第三方许可和无法自动执行的 DNS 操作由用户完成。明确说明缺哪个值、在哪里填写、如何验证；其他不依赖该值的步骤继续执行。

## 执行顺序

1. 运行 `./hosting doctor`。按目标发行版官方说明补依赖；不执行不明来源的安装脚本，不更换用户镜像源。检查 SSH 可用性后再按选定服务设置端口，保留当前 SSH 入口。
2. 收集配置并运行 `./hosting init`。已有部署编辑必要的现有配置，不重新初始化密码/证书。网站有域名时先验证 DNS；中国模式需要实际订阅；TeamSpeak 需要 amd64 和上游许可。
3. `./hosting up` 后检查日志和实际入口。Gitea 用 `./hosting gitea-admin --username ... --email ...` 创建初始管理员；该命令不重置已有账号，生成密码保存到 `runtime/gitea-admin.txt`，提示用户安全保存。Kuma 第一次创建账号可使用已有浏览器工具，缺登录界面能力时给出 SSH 隧道并等待用户完成。TeamSpeak 领取权限密钥并设置服务器密码。按选定访问方式验证 HTTPS/SSH/代理，而非只看 running。
4. 通过 `./hosting configure-backup` 配置 S3、加密密码和故障 webhook，确认加密密码另存密码管理器。只对新备份库执行 `./hosting remote init`，对已有备份库沿用原密码。执行 `./hosting alert-test` 并让用户确认收到，再执行 `./hosting backup --remote` 和 `./hosting remote check`。
5. 按验收标准进行云端下载恢复演练。Gitea 必须验证恢复后的真实仓库和数据库，并 clone/push。测试在独立目录与隔离容器进行，不覆盖生产数据、不暴露恢复副本到公网。
6. 备份成功、告警送达确认、恢复演练完成后执行 `sudo ./hosting schedule install --at 03:30 --timezone Asia/Shanghai`，或用户选择的时刻。检查 timer 已启用、下一次执行时间和一次真实 service 执行结果。系统级备份支持 rootful Docker；rootless 或无 systemd 的机器需专门适配，不能宣布已配置。
7. 输出交付结果：服务 URL、凭据存放位置、代码版本、备份快照 ID/时间、恢复演练证据、定时器下次时间、告警测试结果、用户明确不需要的项和剩余阻塞。将非敏感证据写入目标机 `runtime/deployment-report.md`。不得把未完成的必需项写成成功。

## 失败与迁移

失败先读日志并做有界重试；相同原因连续两次失败就针对原因诊断，不重复消耗资源。权限或凭据问题用提示框/文字请求用户处理，不关闭提交签名或绕过授权。不得通过 `down -v`、递归删除数据、覆盖密码来“修复”。

迁移前先停用旧机 timer：`./hosting schedule disable`。最终执行 `backup --remote --keep-stopped`，记录本次成功快照。新机使用原密码下载到空目录，校验后恢复；先保持原镜像版本，更新地址，再验证业务并切换 DNS。旧机保持停机；新机能恢复和备份后才启用新机 timer。旧机释放前需用户明确授权。
