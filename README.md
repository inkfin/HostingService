# HostingService

在自己的 Linux VPS 上按需启动个人服务。使用 Docker Compose，配置和数据留在各台服务器上，GitHub 只保存部署方式。

## 服务

| 选项 | 服务 | 默认入口 |
| --- | --- | --- |
| `website` | Caddy 静态个人网站，可选自动 HTTPS | 公网 TCP 80 / 443 |
| `gitea` | Gitea，SQLite，关闭公开注册 | 本机 3000；公网 Git SSH 2222 |
| `teamspeak` | TeamSpeak 3 语音服务器 | 公网 UDP 9987、文件传输 TCP 30033 |
| `china` | Mihomo，使用已有 Clash/Mihomo 订阅出站 | 本机 HTTP / SOCKS5 7890，有密码 |
| `overseas` | Hysteria 2 个人代理节点 | 公网 UDP 8443，有密码和证书指纹校验 |
| `uptime-kuma` | 服务可用性监控 | 本机 3001 |
| `it-tools` | 开发者工具箱，如 JSON、时间戳和编码转换 | 本机 8080 |

默认启动网站、Gitea、Uptime Kuma、IT-Tools。两种代理模式手动选一种，不按 IP 自动猜地区。海外模式是自用节点，不含机场售卖、计费或多用户订阅面板。

## 第一次启动

准备好 Linux VPS、Git、Python 3.9+、OpenSSL、Docker Engine 和 Docker Compose v2+，当前用户需要能运行 `docker info`。Docker 安装请按[官方发行版指引](https://docs.docker.com/engine/install/)。本项目不修改系统防火墙、不自动替换镜像源。

多数服务适用于 amd64 / arm64；TeamSpeak 官方镜像只有 amd64。VPS 必须允许运行容器，并提供所用端口的入站连通性。NAT VPS 需要对应端口映射。国内机器还需要能够下载 GitHub 仓库及容器镜像，Mihomo 不能解决首次拉取自身镜像的问题。

私有仓库在 VPS 上需要 GitHub SSH Key 或已登录的 `gh`。使用只读 deploy key 即可拉取：

```bash
git clone git@github.com:inkfin/HostingService.git
cd HostingService
./hosting init && ./hosting up
```

初始化会询问服务列表、代理模式和公网地址。它生成随机代理密码，不覆盖已有 `.env`、代理配置和证书。再次启动只需 `./hosting up`。

带域名的网站示例，DNS 的 A / AAAA 记录需要先指向 VPS：

```bash
./hosting init --services website,gitea,uptime-kuma,it-tools \
  --mode none --host vps.example.com --domain www.example.com
./hosting up
```

未提供 `--domain` 时网站仅使用 HTTP。个人网页放在 `website/`，替换文件即可生效。Caddy 用 TCP 80 / 443 申请并续期 HTTPS 证书；此配置不启用 HTTP/3，UDP 8443 留给 Hysteria。

## 首次设置管理账号

在自己的电脑建立 SSH 隧道，保持命令运行：

```bash
ssh -N -L 3000:127.0.0.1:3000 -L 3001:127.0.0.1:3001 \
  -L 8080:127.0.0.1:8080 user@VPS_IP
```

浏览器打开 `http://localhost:3000`、`http://localhost:3001`、`http://localhost:8080`。Uptime Kuma 第一次打开时创建管理员。

Gitea 已锁定安装并关闭公开注册。在 VPS 执行以下命令进入容器，再创建管理员。密码交互输入，不写入命令历史：

```bash
./hosting compose exec --user 1000 gitea bash
read -r -s -p 'Admin password: ' GITEA_INITIAL_PASSWORD
printf '\n'
gitea admin user create --admin --username personal --email you@example.com \
  --password "$GITEA_INITIAL_PASSWORD" --must-change-password
unset GITEA_INITIAL_PASSWORD
exit
```

在网页上传自己的 SSH 公钥后，用 `ssh://git@VPS_IP:2222/用户名/仓库.git` 访问代码仓库。若要公开 Gitea 网站，可在 `runtime/Caddyfile` 增加以下独立站点，并修改 `.env` 的 `GITEA_ROOT_URL=https://git.example.com/`：

```caddyfile
git.example.com {
    reverse_proxy gitea:3000
}
```

然后执行 `./hosting up` 与 `./hosting compose restart website`。先解析域名并创建管理员，再发布。`ADMIN_BIND` 可以调整管理端口的绑定地址，但日常使用建议保留 SSH 隧道。

## 国内 VPS：通过已有代理出站

```bash
./hosting init --services website,gitea --mode china --host VPS_IP
./hosting up
```

输入 HTTPS 的 Clash/Mihomo 格式订阅 URL，输入内容不会显示。凭据保存在 `runtime/mihomo-access.txt`，完整配置在 `runtime/mihomo.yaml`。订阅只接受节点格式，不能直接使用 V2Ray Base64 列表。

默认全部请求交给订阅节点，节点不可用时不会回退直连。首次选择订阅中的默认节点；修改 `proxy-groups` 可调整选择规则。代理只监听 VPS 本机映射的 7890，不会自动接管宿主机或其他容器的全部流量，也没有启用 TUN。

宿主机程序按需设置 `HTTP_PROXY` / `HTTPS_PROXY`，内容为 `http://personal:密码@127.0.0.1:7890`。其他电脑可以建立 `ssh -N -L 7890:127.0.0.1:7890 user@VPS_IP`，再使用本机 7890 及相同账号密码。容器内的 `127.0.0.1` 指容器自身，如要代理特定容器需单独配置其出站路径。

## 海外 VPS：提供个人代理接入

```bash
./hosting init --services website,gitea --mode overseas --host VPS_IP
./hosting up
```

在云安全组及系统防火墙开放 UDP 8443。把 `runtime/hysteria-client.yaml` 安全复制到自己的电脑，使用 Hysteria 2 客户端启动：

```bash
hysteria client -c hysteria-client.yaml
```

本机 SOCKS5 为 1080，HTTP 代理为 8081。配置文件采用 JSON 语法，属于有效 YAML。

服务端使用生成的自签证书；客户端的 `insecure: true` **必须与 `pinSHA256` 一起保留**，依靠证书指纹验证服务器。支持 Hysteria 2 的图形客户端需确认支持证书指纹固定；不要只勾选跳过证书验证。证书默认有效期 10 年，更换证书时必须同步更新所有客户端指纹。

客户端已包含密码，不能上传 GitHub 或公开分享。修改 `.env` 的 `HYSTERIA_PORT` 后也要同步修改客户端端口。VPS 需允许 UDP；此版本没有内置 TCP 备用代理协议。

## TeamSpeak

首次初始化时选择 `teamspeak`，脚本会检查 CPU 架构并询问是否接受上游许可。无人值守初始化可在阅读许可后添加 `--accept-teamspeak-license`。

通过 `./hosting logs teamspeak` 查看首次启动生成的管理员权限密钥，在 TeamSpeak 客户端连接 `VPS_IP:9987` 后使用。随后在客户端设置服务器密码。查询端口 10011 仅绑定本机，语音和文件传输端口按需在防火墙放行。

## 日常维护

```bash
./hosting check                   # 检查生成文件及 Compose 结构，不拉镜像
./hosting status
./hosting logs gitea
./hosting down                    # 停止容器，保留 data/ 和 runtime/
./hosting up
./hosting backup                  # 短暂停服，打包后恢复原先运行的服务
```

备份包括 `.env`、`runtime/`、`data/`、网站和 Compose 文件，不包括 `backups/` 自身。读取容器数据可能需要 root 权限，在 Linux 系统 Docker 下可用 `sudo ./hosting backup`。备份失败会删除不完整归档并尝试恢复运行中的服务。备份含私钥与密码，务必复制到其他机器并妥善保存。

恢复时先在目标 VPS 克隆本仓库并停止已有容器，把归档解压到仓库根目录，保留所有者及权限，例如 `sudo tar -xzpf /安全路径/备份.tar.gz -C "$PWD"`，再执行 `./hosting check && ./hosting up`。这会覆盖同名配置与数据；请先备份目标机。换 IP、域名、CPU 架构后应检查 `.env`、Caddyfile 和代理客户端配置。

镜像使用明确版本标签，不自动更新。升级前先备份，再修改 `compose.yaml` 的版本，阅读上游迁移说明，执行 `./hosting compose pull && ./hosting up`。标签仍可由上游重新发布，严格复现可进一步固定镜像 digest。

添加现成服务及切换模式见 [config/README.md](config/README.md)。每台 VPS 独立生成配置；不要同步其他机器的 `.env` 来覆盖现有部署。

## 继续扩展

后续可增加 [Syncthing](https://syncthing.net/) 做设备文件同步、[Miniflux](https://miniflux.app/) 订阅 RSS、[Immich](https://immich.app/) 管理照片。前两个适合轻量 VPS；照片服务更适合有足够磁盘的机器。这些尚未加入启动配置。

## 校验与上游文档

```bash
python3 -m unittest discover -s tests -v
bash -n hosting
```

测试覆盖重复初始化、凭据文件权限、订阅转义、证书指纹、ARM 拒绝 TeamSpeak、备份失败恢复及全部 Compose 服务的端口绑定。GitHub Actions 会执行这些检查。配置测试不代表目标 VPS 网络或容器运行验证，首次部署还需查看日志并实际连接。

- [Docker Compose](https://docs.docker.com/compose/)
- [Caddy 自动 HTTPS](https://caddyserver.com/docs/automatic-https)
- [Gitea Docker](https://docs.gitea.com/installation/install-with-docker/)
- [Mihomo 配置](https://wiki.metacubex.one/config/general/)
- [Hysteria 服务端](https://hy2.io/docs/getting-started/Server/)及[客户端证书指纹](https://hy2.io/docs/advanced/Full-Client-Config/)
- [TeamSpeak 官方镜像](https://hub.docker.com/_/teamspeak)
- [Uptime Kuma](https://github.com/louislam/uptime-kuma)
- [IT-Tools](https://github.com/CorentinTh/it-tools)

本仓库保存个人部署配置；各服务的许可按其上游项目执行。
