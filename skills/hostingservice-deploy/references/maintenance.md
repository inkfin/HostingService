# 已有服务器运维

先确认主机、checkout、当前 commit、本地修改、已启用 profiles、实际容器、磁盘、最近成功备份和 timer 状态。读取目标 checkout 的 AGENTS.md、docs/operations.md、config/README.md；以实际 CLI 支持的参数为准。状态检查不需要重跑 setup 的全量问答。

## 接管与重入

区分电脑上的 agent 经 SSH 操作和 VPS 本机 agent。不把 skill 的安装目录当部署路径，也不把本机 Docker 当目标 Docker。仅做日常运维时无需新装模型或 agent；已经能执行远端命令就继续工作。

`.env` 存在时不调用 init。先核对实际状态，再执行缺失步骤；不因历史 report 声称完成就跳过检查。已有 Gitea 管理员不重建；凭据文件存在但账号不存在时检查失败原因，保留文件并核对状态，不生成第二套密码。用户未要求修改的 DNS、端口、代理模式和备份时刻沿用原值。

## 添加、停用服务

1. 保存当前非公开配置，说明变更涉及的端口、数据路径和重启范围。需要变更运行状态或存储前，先执行 `./hosting backup --remote` 并记录实际新快照；若备份未配置或损坏，说明缺项并先修复，不能假称已有回滚点。
2. 已有服务仅未启用：合并 `.env` 中 COMPOSE_PROFILES，不能替换掉其他服务。it-tools 无额外初始配置；Gitea 启动后检查并创建缺失的管理员；website 缺 Caddyfile 时按当前模板生成所选域名/HTTP 配置，保留已有站点内容。TeamSpeak 核对 amd64 和用户接受许可，设置 TS3SERVER_LICENSE=accept。china/overseas 先按需调用 proxy-init，保留已有配置，两种模式不同时启用。
3. 仓库尚未实现的服务：新增 Compose 服务/profile 和镜像固定版本、端口、`data/<service>/` 挂载；同步 scripts/hosting.py 的 SERVICES/PROFILES、validate、所需初始化逻辑、setup 配置/验收以及文档。外部依赖和数据库 dump/恢复需要实现后才能宣称可备份。不能只给 COMPOSE_PROFILES 写一个未知名字。
4. 运行 `./hosting check`，用 `./hosting compose up -d <实际服务名>` 启动新增服务及依赖，避免无关升级。profile 与服务名映射为 china→mihomo、overseas→hysteria，其余同名。验证访问、账号和持久化；新服务再做备份与隔离恢复，更新运行报告。
5. 停用服务：先 `./hosting compose stop <实际服务名>`，再移除 profile。保留数据，检查其他服务仍健康；仅移除 profile 不会停止旧容器。彻底删除数据另按用户明确要求执行。

## 更新代码与镜像

先读当前版本与目标版本差异及上游迁移说明，选定目标 commit/镜像；区别只更新运维文档、增加配置和执行数据库升级。文档更新无需重启容器或停用 timer。

实际部署变更按以下流程执行，每步失败停止后续变更：

1. 记录当前 commit、镜像版本/实际 image ID、私密配置副本、业务状态，以及每个 timer 原来的 enabled/active 状态。临时副本放 runtime 中受限目录。检查是否有手动或 systemd 备份正在运行；需要避免并发时暂停原 active timers，等待已运行的备份结束，不中断归档。维护结束或失败交接时恢复原状态，不能擅自启用原本关闭的 timer。
2. 使用旧版本完成 `./hosting backup --remote`，保存恢复点；不要先升级 Gitea 再做唯一备份。
3. `git fetch origin` 后核对 diff 和目标版本。只有本地状态允许时才做 `git merge --ff-only <选定commit>`（跟随 main 时也可 git pull --ff-only）。有本地定制则保留并合并，不 reset/clean。不要在 diff 输出中暴露私密配置。
4. `./hosting check` 通过后，针对受影响的服务执行 `./hosting compose pull <服务名>`、`./hosting compose up -d <服务名>`。需要全栈升级时才执行全栈 pull/up。依赖关系与升级顺序以服务要求为准。
5. 验证实际访问和业务、日志、数据持久性；更新后的备份与必要的恢复演练通过后恢复原 timer 状态。若 scheduler 文件/部署路径改变，按记录的原时区和时间重新生成 units 并检查下次执行时间；不能回退到默认 03:30。检查 backup-health，并记录变更结果与待办。

失败回滚先判断是否发生数据迁移。纯配置变更可恢复保存的配置和原镜像；已变更数据库格式时，保持失败现场，从升级前快照在隔离环境恢复原版本并验证，再切换生产。恢复会丢失备份之后的新写入，涉及覆盖生产或数据损失时说明具体范围并取得用户授权。保留可用的原镜像/快照，验收前不 prune。

## 故障和备份

先用 status、服务定向 logs、schedule status、backup-health 和 systemd journal 定位原因。分享日志前隐去 token/密码/订阅；Compose 渲染也可能含敏感变量，不整段回显。针对根因做有界重试，禁止通过清空数据或重新生成凭据解决启动失败。

备份配置更改后验证新快照、通知接收和恢复能力。日常定时任务由 systemd 执行；skill 本身没有定时执行能力。整机离线告警需要独立外部监控。现有代码不自动清理备份；容量不足先诊断，不随意删除 restic 内部对象。迁移加载 checkout 的 docs/storage-and-migration.md，沿用原 restic 密码并控制旧机写入。
