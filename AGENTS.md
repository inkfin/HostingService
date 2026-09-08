# HostingService

这是公开的个人 VPS 部署仓库。操作真实服务前确认目标主机和部署目录；当前 checkout 也可能只是开发副本。用户要求部署、添加服务或更新后，执行已授权且可自动完成的工作，只询问缺失的信息或新的破坏性操作。

## 入口与上下文

- 部署、增删服务、升级、备份、故障排查和迁移，读取 `skills/hostingservice-deploy/SKILL.md`，按任务加载其 references。
- `.agents/skills/hostingservice-deploy` 链接到同一份 skill；只修改 `skills/` 下的源文件，不复制成两套。
- `install.sh` 默认安装系统依赖、Homebrew 和 OpenCode，引导普通用户完成模型登录并打开仓库；不安装 Codex、不自动拉取已有 checkout 的更新。`--manual` 跳过 agent 并调用 `./hosting setup`。
- `./hosting setup` 是人类终端向导。agent 可直接调用 `./hosting` 子命令完成同样工作；不要批量输入 yes 冒充人工访问或恢复验收。
- `./hosting --help`、实际代码和目标机状态决定命令能力。不存在 `hosting add` / `hosting update`，不要编造这些命令。

## 数据和变更

`.env`、`runtime/`、`data/`、`backups/` 只留在目标机，已被 Git 忽略。不要输出密钥、订阅或 webhook token，也不要把 agent 登录凭据复制进项目。OpenCode 模型凭据属于运行它的普通用户 home，不纳入服务备份；不要以 root 启动 Homebrew/OpenCode。公开的 `website/`、文档和例子不可包含私密数据。

保留已有密码、证书、配置和持久化数据。新增 profile 要合并现有选择；停用 profile 前先停止对应容器。不能用 `init` 重置部署，不能用 `down -v`、清库或 `git reset --hard` 修复问题。

升级前记录版本、保存配置、完成备份，检查上游迁移要求及回滚可行性。修改前读取 diff；有本地改动时先处理合并，不能覆盖。已迁移的数据库不能只通过降低镜像版本回滚。修改 SSH/防火墙时保留当前管理入口，并从第二个连接验证。

持久化服务默认 bind mount 到 `data/<service>/`；外部数据库、named volume、额外挂载及远程对象存储需要额外备份设计。纳入 tar 不等于应用数据一致或恢复可用。Gitea 当前仅支持本地 SQLite，使用现有停服检查和恢复流程。

自动备份由 VPS systemd timer 执行，不依赖 agent 常驻。运维时保留原定时策略，避免与正在运行的备份并发修改数据或代码。清理历史快照/释放旧 VPS 需属于用户明确授权范围。

## 验证与交付

- Python/备份逻辑变更：`python3 -m unittest discover -s tests -v`；restic 集成和 Linux systemd 验证需要相应环境。
- Shell 变更：`bash -n hosting install.sh scripts/agent-bootstrap.sh`。
- Gitea 备份/恢复逻辑变更：在隔离 Linux Docker 环境运行 `tests/gitea_e2e.py`，不连接生产数据。
- skill/文档变更：检查相对链接、skill frontmatter 和 CLI 发现结果；不必重跑与修改无关的部署测试。
- 服务验收检查实际入口和业务操作。报告区分自动检测、人工确认、合成测试、生产恢复演练；容器 running 不代表完成。

实际地址、版本、备份快照、恢复证据及待办写入目标机 `runtime/deployment-report.md`，不能回填到公开 `AGENTS.md`。本文件是仓库说明，不是服务器 home 的全局指令或 agent 自动运行配置。
