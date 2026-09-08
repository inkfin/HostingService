# Agent 部署与配置

## 安装 skill

在运行 agent 的电脑上准备 Node.js/npm：

```bash
npx skills add https://github.com/inkfin/HostingService.git \
  --skill hostingservice-deploy --agent codex --global
```

也可以替换 `--agent codex` 为 Skills CLI 支持的其他 agent。仓库公开，下载无需 GitHub 凭据。部分 agent 需要刷新或重新开启会话才能发现新 skill。

安装后给 agent 一个明确请求：

> 使用 hostingservice-deploy skill，把 HostingService 部署到我的 SSH 主机 vps，目录 /opt/HostingService。带我配置所有必填项，做好 Gitea、对象存储、每天备份及恢复验证。缺少信息时用提示框问我。

安装不会自动启动 agent 或登录服务器。agent 已有 SSH 工具和授权时自动执行可完成的工作；遇到非敏感配置问题优先使用宿主的表单/提示框，没有该能力则文字询问。密钥通过保密输入或目标终端填写，不放进普通聊天。

## 实际配置阶段

1. **目标检查**：新装还是迁移、SSH、Linux、CPU、磁盘、已有数据、端口。`./hosting doctor` 列出依赖缺项。按发行版官方说明装好 Docker/Compose/Python/OpenSSL/Git/restic/systemd。
2. **选择服务**：网站域名或 IP；Gitea 公网 HTTPS 或 SSH 隧道；代理模式；TeamSpeak 架构与许可。`./hosting init` 不覆盖已有配置。
3. **启动与账号**：`./hosting up`，检查日志和入口。`./hosting gitea-admin --username personal --email you@example.com` 生成账号及随机初始密码到 `runtime/gitea-admin.txt`；首次登录更改密码并上传 SSH 公钥。Kuma 需创建账号/监控，TeamSpeak 需领取管理员密钥及设置服务器密码。
4. **云端备份**：在用户自己的终端执行 `./hosting configure-backup` 隐藏输入密钥。首次新库 `remote init`，已有库沿用原密码。密码另存密码管理器。运行 `alert-test` 并确认收到，运行 `backup --remote`、`remote check`。
5. **恢复演练**：从实际对象存储取回此次快照，在隔离环境恢复 Gitea 并验证账号、仓库、Issue、clone/push；附件/LFS 如使用也要验证。详细操作见备份与迁移文档。
6. **定时运行**：`sudo ./hosting schedule install --at 03:30 --timezone Asia/Shanghai`。查看 timer 下次执行时间，实际执行一次 service，确认新云端快照与健康状态。
7. **交付**：报告实际访问地址、私密凭据位置、快照、恢复结果、timer 下次执行时间、告警送达及剩余问题。缺少必需步骤时标记未完成。

默认建议每天备份；最大可丢失时间约为距离最近一次成功备份的间隔，不保证零数据丢失。时间和时区均可调整。网站 DNS 和云安全组的修改以用户选定厂商的实际工具能力为准。

参考：[Skills CLI 安装格式与私有仓库](https://github.com/vercel-labs/skills)、[OpenAI skills](https://developers.openai.com/codex/skills/)。

## 无 agent 部署

白板 VPS 使用 [README 一键命令](../README.md#白板-vps-一键配置)。`install.sh` 安装系统依赖，`./hosting setup` 负责可重复运行的配置向导。已有 agent 也可以调用这些入口，并协助完成外网访问和实际恢复验收。
