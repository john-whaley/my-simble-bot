# VS Code 本地运行说明

这份说明适用于 Windows + VS Code，本地直接运行 `my-simple-bot`，不走 Docker。

## 1. 前置条件

- 已安装 Python 3.9 及以上
- 已安装 VS Code
- VS Code 已安装 Python 扩展
- 当前网络可以访问 Telegram API

建议优先使用 Python 3.11 或 3.12。

## 2. 用 VS Code 打开项目

1. 打开 VS Code
2. 点击 `文件 -> 打开文件夹`
3. 选择目录 `F:\我的项目\my-simple-bot`

## 3. 创建虚拟环境

项目之前的其它目录里用过旧版 `python-telegram-bot 11.1.0`，这和当前项目需要的 `21+` 版本不兼容。

所以本地运行时，建议一定要先创建虚拟环境。

## 6. 安装依赖

确认终端已经在虚拟环境里，再执行：

```powershell
pip install -r requirements.txt
```

## 7. 配置机器人

打开文件 [config.ini](/f:/我的项目/my-simple-bot/config.ini:1)，填写你自己的配置：

```ini
[BOT]
TOKEN = 你的机器人 Token
CHANNEL_ID = -100xxxxxxxxxx
CHANNEL_LINK = @your_channel
ADMIN_IDS = 123456789,987654321

[FILTER]
BLACKLIST = 敏感词1,敏感词2,敏感词3
```

说明：

- `TOKEN` 从 `@BotFather` 获取
- `CHANNEL_ID` 推荐填写频道真实 ID，网页版进入频道右键检查，格式一般是 `-100xxxxxxxxxx`
- `ADMIN_IDS` 用户 ID，支持多个，用英文逗号分隔
- `BLACKLIST` 使用英文逗号分隔

## 8. 给机器人频道权限

运行前请确认：

- 机器人已经被加入目标频道
- 机器人在频道里有发消息权限

## 10. 在 VS Code 里运行

有两种方式。

### 方式一：终端运行

在 VS Code 终端执行：

```powershell
   python main.py
```

看到类似日志就说明启动成功：

```text
Bot is starting with 2 admin(s) configured
```

### 方式二：直接点运行按钮

1. 打开 [main.py](/f:/我的项目/my-simple-bot/main.py:1)
2. 点击右上角的 `Run Python File`

前提仍然是你已经选中了 `.venv` 解释器。

## 12. 数据和日志位置

运行后会自动生成：

- 数据库：[data/messages.db](/f:/我的项目/my-simple-bot/data/messages.db)
- 日志：[logs/bot.log](/f:/我的项目/my-simple-bot/logs/bot.log)

如果文件还没出现，通常是因为程序还没真正启动成功，或者还没有处理过消息。

## 13. 常见问题

### 1. 提示 `python-telegram-bot 11.1.0`

原因：

- 你用了系统全局 Python
- 没有切换到 `.venv`

解决：

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip show python-telegram-bot
```

### 2. 启动成功但发不出去

常见原因：

- `TOKEN` 错了
- `CHANNEL_ID` 错了
- 机器人没被加到频道
- 机器人没有发消息权限
- 当前网络无法访问 Telegram API

### 3. 配置改了但没生效

修改 [config.ini](/f:/我的项目/my-simple-bot/config.ini:1) 后，需要重启程序。

## 14. 推荐的本地运行命令

以后每次本地启动，按这个顺序就可以：

```powershell
cd F:\我的项目\my-simple-bot
.\.venv\Scripts\Activate.ps1
python main.py
```
