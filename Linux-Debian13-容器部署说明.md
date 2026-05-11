# Linux / Debian 13 容器部署说明

## 3. Debian 13 安装 Docker 和 Compose

下面这套方式基于 Docker 官方 Debian 安装文档，适合 Debian 13。

先更新系统并安装基础工具：

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
```

删除可能冲突的旧包：

```bash
sudo apt remove -y docker.io docker-compose docker-doc podman-docker containerd runc
```

添加 Docker 官方仓库：

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
```

```bash
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
```

安装 Docker Engine 和 Compose 插件：

```bash
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

验证安装：

```bash
sudo docker --version
sudo docker compose version
```

可选：把当前用户加入 `docker` 组，后续可以少写 `sudo`：

```bash
sudo usermod -aG docker $USER
```

执行后重新登录一次 SSH 会话再生效。

## 4. 在 VPS 上拉取项目

进入你打算存放项目的目录，例如：

```bash
cd /opt
sudo git clone https://github.com/你的用户名/你的仓库名.git my-simple-bot
sudo chown -R $USER:$USER /opt/my-simple-bot
cd /opt/my-simple-bot
```

## 5. 准备运行配置

从示例文件复制一份正式配置：

```bash
cp config.ini.example config.ini
```

然后编辑：

```bash
nano config.ini
```

至少要填写：

- `TOKEN`
- `CHANNEL_ID`
- `ADMIN_IDS`
- `USER_MESSAGE_COOLDOWN`
- `BLACKLIST`

## 6. 准备持久化目录

虽然容器启动时也会创建目录，但建议先在宿主机准备好：

```bash
mkdir -p data logs
```

这些目录会通过 `docker-compose.yml` 挂载到容器内：

- `./data` -> `/app/data`
- `./logs` -> `/app/logs`
- `./config.ini` -> `/app/config.ini`

## 7. 启动容器

首次启动建议带上构建参数：

```bash
sudo docker compose up -d --build
```

查看容器状态：

```bash
sudo docker compose ps
```

查看日志：

```bash
sudo docker compose logs -f
```

## 8. 更新部署

以后项目更新后，在 VPS 上执行：

```bash
cd /opt/my-simple-bot
git pull
sudo docker compose up -d --build
```

如果只想重启：

```bash
sudo docker compose restart
```

如果想停止：

```bash
sudo docker compose down
```

## 9. 当前容器方案的兼容性说明

本项目已经做了这些适配，方便在 GitHub 和 Debian 13 VPS 上部署：

- `config.ini` 默认不提交到 GitHub
- 运行数据和日志目录支持宿主机挂载持久化
- 增加了 `.dockerignore`，构建镜像时不会把本地虚拟环境、数据库、日志打进镜像
- 镜像里补了 `ca-certificates` 和 `tzdata`
- `docker-compose.yml` 使用 `restart: unless-stopped`
- `docker-compose.yml` 配置了日志滚动，避免容器日志无限膨胀
- 程序支持通过 `CONFIG_FILE` 环境变量指定配置文件路径

## 10. 网络与防火墙说明

这个机器人当前使用的是 Telegram 轮询模式，不需要对外开放 HTTP 端口。

通常只需要保证 VPS：

- 能正常访问外网
- 能访问 `api.telegram.org`

如果你的服务器出站网络受限，需要先解决网络连通性，否则容器能启动但机器人无法正常工作。

## 11. 常用排查命令

查看最近日志：

```bash
sudo docker compose logs --tail=200
```

进入容器：

```bash
sudo docker compose exec bot sh
```

查看配置文件是否挂载成功：

```bash
sudo docker compose exec bot ls -l /app
sudo docker compose exec bot cat /app/config.ini
```

查看数据库文件是否已生成：

```bash
ls -l data
```

## 12. 参考资料

- Docker 官方 Debian 安装文档：https://docs.docker.com/engine/install/debian/
- Docker Compose 命令文档：https://docs.docker.com/reference/cli/docker/compose/
