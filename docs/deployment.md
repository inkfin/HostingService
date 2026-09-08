# Agent 部署与运维

## 脚本、agent 与 skill 的分工

白板机需要先具备 SSH 管理入口、可用网络和系统依赖。若选择在 VPS 运行 agent，还需要安装该 agent 并完成模型登录/API 认证；若电脑上的 agent 已能 SSH 操作 VPS，这些模型配置只留在电脑上。模型凭据、SSH 密钥和备份凭据用途不同，不应混在部署仓库里。

当前 `install.sh` 安装 Docker 等依赖后进入人类配置向导，**不安装 agent、Node.js 或模型认证，不自动建立出站代理或修改防火墙**。它要求 GitHub、软件源和镜像可达。`./hosting init` 仅生成业务服务配置；代理自身尚未安装时无法解决首次下载问题。不要把执行完安装脚本当成 agent 已经可用。

agent 接手后的任务是读取实际状态，调用现有部署命令，完成新增服务、更新、排错和恢复。用户仍需完成无工具权限的云操作、登录、密钥保密输入和许可接受。日常备份由 systemd 定时执行，不依赖 agent 常驻或模型额度。

## skill 安装在哪台机器

`npx skills` 是第三方 Skills CLI；本项目没有发布独立 npm 包，skill 源码位于 GitHub 的 `skills/hostingservice-deploy/`。安装命令在哪台机器、哪个用户下执行，skill 就安装给那里的 agent。`--global` 表示当前用户跨项目可用，不是安装到远端 VPS，也不是全系统用户共享。

| 使用方式 | skill 位置与目标 |
| --- | --- |
| 电脑上的 agent 经 SSH 运维 | 在电脑上安装 skill；目标仍是指定 VPS |
| VPS 本机 agent 运维 | 在运行 agent 的 VPS 用户下安装；sudo/root 的 home 与普通用户不同 |
| 在仓库目录启动 Codex | checkout 自带 `.agents/skills/hostingservice-deploy` 相对符号链接，指向 `skills/` 的唯一源码；无需 npx |

Codex 支持仓库 `.agents/skills/` 及用户 `~/.agents/skills/` 下的 skill 和符号链接。Skills CLI 的具体目标路径/链接布局可能随版本变化，以安装输出和 `npx skills list --global` 为准。参见 [Codex skill 加载规则](https://developers.openai.com/codex/skills/)、[Skills CLI 安装范围](https://github.com/vercel-labs/skills#installation-scope)。

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

参考：[Skills CLI 安装格式](https://github.com/vercel-labs/skills)、[OpenAI skills](https://developers.openai.com/codex/skills/)。

## 无 agent 部署

白板 VPS 使用 [README 一键命令](../README.md#白板-vps-一键配置)。`install.sh` 安装系统依赖，`./hosting setup` 负责可重复运行的配置向导。已有 agent 也可以调用这些入口，并协助完成外网访问和实际恢复验收。

## AGENTS.md 与日常运维

仓库根目录 `AGENTS.md` 保存项目入口、配置与数据边界、测试和运维要求，不保存具体服务器地址或密钥。Codex 从项目根目录到当前工作目录加载项目指令；所以在 VPS 上用已安装且登录的 Codex 时，从 `/opt/HostingService` 启动即可。电脑上的 agent 通过 SSH 操作时，skill 要求先显式读取远端 AGENTS.md，远端文件不会自动变成本地全局指令。参见 [AGENTS.md 官方规则](https://developers.openai.com/codex/guides/agents-md/)。

本项目不修改 `~/AGENTS.md`，避免影响服务器上的其他项目。AGENTS.md 不会安装、启动或登录 agent，也不会赋予 sudo/云平台权限。实际运行报告保存在目标机 `runtime/deployment-report.md`。

之后可直接告诉 agent：

> 使用 hostingservice-deploy，在 vps 的 /opt/HostingService 加上 Uptime Kuma，保留已有服务、密码和备份策略，验证后告诉我访问入口。

> 使用 hostingservice-deploy，检查 vps 的备份失败原因并修复，确认产生了新的异地快照。

> 使用 hostingservice-deploy，将 vps 上的部署更新到我指定的版本；先备份，保留本地定制和定时策略，验证后交付。

运维步骤见 [skill 运维流程](../skills/hostingservice-deploy/references/maintenance.md)。它调用现有命令并按需编辑配置，不依赖尚未实现的 `hosting add` 或 `hosting update`。

仓库更新会同步仓库内的 AGENTS.md 和 skill 链接目标。通过 npx 安装到用户目录的 skill 是另一份安装，需要单独刷新；最明确的方式是重新运行上面的定向安装命令，并检查安装结果。更新 skill 不会更新 VPS 代码、拉取容器镜像或自动执行运维。
