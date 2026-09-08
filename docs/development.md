# 开发约定与验收

## 产品目标

让用户通过安装 `hostingservice-deploy` skill，授权 agent 在指定 Linux VPS 上完成选定服务、初始账号、对象存储备份、通知及服务器定时器的配置，并实际验证访问与恢复。不是执行 Compose 后立即宣布成功。

代码在 GitHub；业务容器在用户 VPS；持久化数据在 VPS 的 bind mount；加密备份在用户指定对象存储。`npx skills add` 只安装 skill，不购买资源、不启动 agent、不自动部署服务。所有真实目标配置与业务凭据均在 `runtime/` / `.env`，禁止提交。

## 模块

- `compose.yaml`：服务、镜像版本、端口、挂载。
- `scripts/hosting.py`：部署、凭据初始化、本地备份、restic、通知、健康检查。
- `scripts/reliability.py`：停服后 Gitea 存储覆盖检查、SQLite integrity_check、git fsck、健康状态。
- `scripts/scheduler.py`：systemd units 渲染、安装、查询、停用。
- `skills/hostingservice-deploy/`：可独立安装的 agent walkthrough，不能假定安装目录就是部署源码。
- `tests/`：单元/集成测试；`tests/gitea_e2e.py` 是独立 Docker 恢复测试，不属于普通单测。

## 备份不变量

1. 当前完整 Gitea 备份范围限定为 SQLite + `/data` 内本地存储；外部数据库、外部路径、外部符号链接或非本地 storage 配置应阻止“完整”备份。
2. 停止服务后才校验和归档；Gitea 强制终止/OOM 或错误挂载不能算成功。所有命令错误必须返回非零。
3. tar 包含完整业务目录、配置、脚本及备份 manifest，校验文件是检测传输损坏，不代替应用恢复。
4. 备份失败尝试恢复原运行服务；日常上传在恢复服务后执行；迁移成功后保持旧服务停止。
5. 只有 restic 成功后更新 last_remote_success。失败写状态并尝试发告警；不能用本地归档存在或上一次云端快照冒充本次成功。
6. 定时器不依赖 agent 在线。每天备份、每小时检查新鲜度、每周检查远端结构；实际云端和告警先验证，再安装。
7. 恢复进入空目录，不直接覆盖线上业务。自动删除云端历史不在本版范围；不得用对象存储生命周期随意删除 restic 内部对象。

## 部署状态

阶段为 preflight → configuration → services → accounts → offsite-backup → restore-drill → scheduling → ready。每阶段可为 pending / blocked / verified / not-applicable。agent 记录实际命令结果及时间于目标机 `runtime/deployment-report.md`；只有所有必需阶段 verified 才能交付 ready。

“HTTP 2xx 接受通知”和“用户确认收到通知”是两个证据；容器 running 与业务登录/clone/push 也是两个证据。不能靠手动勾选一个状态文件代替实际验证。

## 测试

```bash
python3 -m unittest discover -s tests -v
bash -n hosting
python3 scripts/scheduler.py render
python3 tests/gitea_e2e.py
```

Gitea E2E 需要可用 Docker 引擎和镜像下载，使用隔离项目和临时目录，只绑定 loopback。必须覆盖：创建账号/仓库/Issue、Git push、运行真实备份校验、恢复到新的数据目录与容器、登录/Issue/commit 核验、恢复后的 clone/push。不要借用生产库做写入测试。

CI 配置测试与 E2E 分开；出现网络阻塞时报告未完成，不能把 skip 当作通过。skill 的实际安装需用 `npx skills add <本地路径> --list` 验证发现，再在临时目录安装检查文件完整性。

## 已知边界

- 备份是全量压缩归档，数据量变大后停机和上传开销增长；SQLite 适合个人规模，不承诺大型团队高可用。
- 当前不实现备份保留清理，需监控磁盘/对象存储容量。后续用 restic forget/prune 增加可审计保留策略。
- 系统级定时器针对 rootful Docker + systemd。rootless / OpenRC 需另行适配。
- 机内告警无法覆盖整台 VPS 掉线，需要用户独立外部监控；通知失败会在 journal 中留下错误。
- GUI 提示依赖 agent 宿主能力，skill 自身不是 GUI 应用。

## 白板安装与可续跑向导

`install.sh` 支持 Debian 12/13、Ubuntu 22.04/24.04 的 amd64/arm64 systemd 主机。使用发行版 apt 安装基础依赖、Docker 官方 apt 源安装 Engine/Compose；存在冲突包时停止，不自动卸载。既有 Docker 必须通过 daemon 和 Compose 检查。安装路径固定 `/opt/HostingService`，临时克隆成功后才移动到目标目录。既有 checkout 不自动更新或重置。

`./hosting setup` 调用现有 init、check、up、管理员创建、对象存储、backup、remote check 和 schedule 命令。配置文件决定续跑位置，每次重查运行状态；人工验收必须重新确认。Ctrl-C/EOF/命令失败返回非零，不报告完成。密钥沿用隐藏输入和 0600 文件，初始化不会覆盖旧值。setup-report 明确区分程序检查和人工确认；不把用户确认伪装为自动恢复测试。

安装器不会获取云账号、自动购买存储、改变防火墙或恢复覆盖数据。迁移先按现有手册恢复，再续跑向导。当前恢复演练由向导引导人工执行；不能将首次备份、restic read-data 校验或 CI 的合成 Gitea 测试视为用户真实数据已完成恢复演练。

默认 bootstrap 在依赖阶段后调用 `scripts/agent-bootstrap.sh`，为普通用户安装 Homebrew/OpenCode、引导模型登录并打开仓库。`scripts/agent_checkout.py` 只移交公开代码和 Git 元数据，遇到被跟踪的私密路径直接失败，保留容器数据 UID。`--manual` 继续调用原 setup。重跑不会自动升级 brew formula、重新生成模型密钥或重置服务。模型提供商可用性需目标机实际验证，不通过合成测试推断。
