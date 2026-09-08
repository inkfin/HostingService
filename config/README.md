# 配置与数据

`compose.yaml` 中的镜像版本、端口和挂载路径可直接修改并提交。
`./hosting init` 生成的 `.env` 和 `runtime/` 含服务器地址、订阅、密码及证书，只留在服务器上。
运行数据在 `data/`，备份在 `backups/`，均不会进入 Git。

个人网站内容在 `website/`，修改后会进入 Git。不要往这里放凭据或私人文件。

已有机器不要重新初始化。增删 `.env` 的 `COMPOSE_PROFILES` 后执行 `./hosting up`。
添加 china / overseas 模式前，先使用 `./hosting proxy-init china` 或 `./hosting proxy-init overseas` 生成配置。
停用服务前执行 `./hosting compose stop 服务名`，再删除 profile；不会自动删除数据。

对象存储模板为 `config/restic.example.json`，实际凭据放在 `runtime/restic.json`，加密密码放在 `runtime/restic-password`。两者权限设为 600，密码必须另存密码管理器。详见[对象存储备份](../docs/object-storage.md)。

新增服务的数据挂载放到 `data/服务名/` 才会被当前备份覆盖；另加的 named volume、外部目录或远程数据库需要单独设计备份。
