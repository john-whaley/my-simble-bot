# Linux / Debian 13 容器部署说明

本文档只说明：

- 如何把项目拉到 Debian 13 VPS
- 如何运行初始化脚本批量生成机器人配置
- 如何使用 `msb` 再次启动配置脚本
- 如何使用 Docker Compose 启动和更新

不包含机器人管理员或普通用户的操作说明。

## 1. 安装 Docker、Compose 和 Python

先安装基础工具：

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

安装 Docker Engine、Compose 插件和 Python：

```bash
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin python3
```

验证：

```bash
sudo docker --version
sudo docker compose version
python3 --version
```

可选：如果你不想每次都写 `sudo`：

```bash
sudo usermod -aG docker $USER
```

执行后重新登录一次 SSH 会话。

## 2. 拉取代码

示例：

```bash
cd /opt
sudo git clone https://github.com/你的用户名/你的仓库名.git my-simple-bot
sudo chown -R $USER:$USER /opt/my-simple-bot
cd /opt/my-simple-bot
```

## 3. 首次运行配置脚本

首次建议直接运行：

```bash
python3 setup_bots.py
```

脚本会先询问：

```text
是否增加机器人？1. 保持默认，结束脚本  2. 增加
```

说明：

- 默认是 `1`，也就是直接结束脚本，防止误触
- 你要真正新增机器人时，输入 `2`

## 4. 配置流程

选择增加机器人后，脚本会循环询问：

1. 机器人名称
   - `1` 使用默认名称，例如 `bot1`、`bot2`
   - `2` 自定义机器人名称
2. 依次填写：
   - `CHANNEL_ID`，必填
   - `CHANNEL_LINK`，必填，格式必须是 `@***`
   - `ADMIN_IDS`，选填，默认空
   - `USER_MESSAGE_COOLDOWN`，选填，默认 `30`
   - `BLACKLIST`，选填，默认空
3. 继续增加机器人时，脚本会再问：
   - `1` 默认不增加，结束循环
   - `2` 继续增加

## 5. 数据库与目录行为

每增加一个机器人，脚本都会为它准备独立目录：

- `data/<机器人名>/`
- `logs/<机器人名>/`

并检查该机器人对应的数据库文件：

- 如果 `data/<机器人名>/messages.db` 不存在，就创建
- 如果已经存在，就直接保留，不覆盖、不清空

这意味着：

- 每个机器人都有自己的数据库
- 以后重新运行脚本新增机器人时，不会破坏已存在机器人的历史数据

## 6. 脚本生成的文件

脚本会自动生成或更新：

- `config1.ini`
- `config2.ini`
- `docker-compose.yml`
- `.msb-bots.json`

其中：

- `configN.ini` 是每个机器人的独立配置
- `docker-compose.yml` 会根据当前所有机器人重写
- `.msb-bots.json` 是本地清单文件，用来记住已经配置过的机器人

## 7. 配置 `msb` 命令

项目里已经带了一个启动脚本文件：

- `msb`

建议在 Debian 13 上执行一次：

```bash
chmod +x msb
sudo ln -sf /opt/my-simple-bot/msb /usr/local/bin/msb
```

这样后面你在项目目录里或系统里都可以直接输入：

```bash
msb
```

它会再次启动机器人配置脚本。

## 8. 后续增加机器人

当你以后想继续增加机器人时：

```bash
cd /opt/my-simple-bot
msb
```

脚本会先读取 `.msb-bots.json` 中已有的机器人配置，再决定是否新增。

默认选项依然是：

```text
1. 保持默认，结束脚本
2. 增加
```

这样可以避免误输入时直接改动现有部署。

## 9. 启动容器

配置完成后执行：

```bash
sudo docker compose up -d --build
```

查看状态：

```bash
sudo docker compose ps
```

查看日志：

```bash
sudo docker compose logs -f
```

如果只看某个机器人，例如 `bot1`：

```bash
sudo docker compose logs -f bot1
```

## 10. 更新代码后的流程

以后更新项目时：

```bash
cd /opt/my-simple-bot
git pull
```

如果你需要继续增加机器人：

```bash
msb
```

然后重新构建并启动：

```bash
sudo docker compose up -d --build
```

## 11. 停止、重启和进入容器

停止：

```bash
sudo docker compose down
```

重启：

```bash
sudo docker compose restart
```

进入某个机器人容器：

```bash
sudo docker compose exec bot1 sh
```

## 12. 当前部署方案的兼容性说明

本项目当前已经适配这些点：

- 支持 `setup_bots.py` 交互生成多机器人配置
- 支持通过 `msb` 再次启动配置脚本
- 每个机器人有独立的配置文件、数据目录、日志目录和数据库
- 已有数据库存在时会自动保留，不覆盖
- `config.ini`、`config1.ini`、`config2.ini`、`.msb-bots.json` 默认不会提交到 GitHub
- `.dockerignore` 会排除本地配置、数据库、日志和虚拟环境
- 容器使用只读根文件系统、PID 限制、内存限制和 CPU 限制
- 容器启用了日志滚动，避免日志无限膨胀

## 13. 常用排查命令

查看最近日志：

```bash
sudo docker compose logs --tail=200
```

查看某个服务：

```bash
sudo docker compose logs --tail=200 bot1
```

查看生成的配置文件：

```bash
ls -l config*.ini
cat config1.ini
```

查看数据库文件：

```bash
find data -maxdepth 2 -name messages.db
```

查看本地机器人清单：

```bash
cat .msb-bots.json
```

## 14. 参考资料

- Docker 官方 Debian 安装文档：https://docs.docker.com/engine/install/debian/
- Docker Compose 命令文档：https://docs.docker.com/reference/cli/docker/compose/
