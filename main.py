#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import configparser
import html
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Awaitable, Callable, Optional, TypeVar, Union

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.getenv("CONFIG_FILE", str(BASE_DIR / "config.ini"))).resolve()
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
DB_PATH = DATA_DIR / "messages.db"
LOG_PATH = LOG_DIR / "bot.log"

logger = logging.getLogger(__name__)
MESSAGE_COOLDOWNS: dict[int, float] = {}
T = TypeVar("T")


@dataclass(frozen=True)
class Settings:
    token: str
    channel_id: Union[int, str]
    channel_link: str
    admin_ids: set[int]
    blacklist_words: list[str]
    user_message_cooldown: int


def ensure_runtime_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging() -> None:
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    stream_handler = logging.StreamHandler()
    file_handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )

    stream_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)

    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)


def parse_channel_id(raw_value: str) -> Union[int, str]:
    value = raw_value.strip()
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def normalize_channel_link(raw_value: str) -> str:
    value = raw_value.strip()
    if not value:
        return ""
    if not value.startswith("@"):
        raise ValueError("BOT.CHANNEL_LINK must start with @, for example @your_channel.")
    if not re.fullmatch(r"@[A-Za-z0-9_]{4,}", value):
        raise ValueError("BOT.CHANNEL_LINK format is invalid.")
    return value


def parse_blacklist(raw_value: str) -> list[str]:
    return [word.strip() for word in raw_value.split(",") if word.strip()]


def parse_admin_ids(raw_value: str) -> set[int]:
    admin_ids: set[int] = set()
    for item in raw_value.split(","):
        value = item.strip()
        if not value:
            continue
        if not re.fullmatch(r"\d+", value):
            raise ValueError(
                "BOT.ADMIN_IDS contains an invalid value. "
                "Use comma-separated numeric Telegram user IDs."
            )
        admin_ids.add(int(value))
    return admin_ids


def load_settings() -> Settings:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Config file not found: {CONFIG_PATH}. "
            "Create config.ini from config.ini.example first."
        )

    config = configparser.ConfigParser()
    config.read(CONFIG_PATH, encoding="utf-8")

    token = config.get("BOT", "TOKEN", fallback="").strip()
    channel_id_raw = config.get("BOT", "CHANNEL_ID", fallback="").strip()
    channel_link_raw = config.get("BOT", "CHANNEL_LINK", fallback="").strip()
    admin_ids_raw = config.get("BOT", "ADMIN_IDS", fallback="").strip()
    blacklist_raw = config.get("FILTER", "BLACKLIST", fallback="")
    cooldown_raw = config.get("BOT", "USER_MESSAGE_COOLDOWN", fallback="30").strip()

    if not token or token == "your_bot_token_here":
        raise ValueError("BOT.TOKEN is missing or still using the example value.")
    if not channel_id_raw or channel_id_raw == "your_channel_id_here":
        raise ValueError(
            "BOT.CHANNEL_ID is missing or still using the example value."
        )

    admin_ids = parse_admin_ids(admin_ids_raw)

    try:
        cooldown_seconds = max(0, int(cooldown_raw or "30"))
    except ValueError as exc:
        raise ValueError("BOT.USER_MESSAGE_COOLDOWN must be an integer.") from exc

    return Settings(
        token=token,
        channel_id=parse_channel_id(channel_id_raw),
        channel_link=normalize_channel_link(channel_link_raw),
        admin_ids=admin_ids,
        blacklist_words=parse_blacklist(blacklist_raw),
        user_message_cooldown=cooldown_seconds,
    )


def get_db_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def build_httpx_request(*, read_timeout: float) -> HTTPXRequest:
    return HTTPXRequest(
        connection_pool_size=32,
        connect_timeout=20.0,
        read_timeout=read_timeout,
        write_timeout=20.0,
        pool_timeout=10.0,
        http_version="1.1",
    )


def init_db() -> None:
    with get_db_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT NOT NULL,
                last_name TEXT,
                message_text TEXT NOT NULL,
                is_published INTEGER NOT NULL DEFAULT 0,
                is_blocked INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL,
                published_at DATETIME
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                reason TEXT NOT NULL,
                banned_at DATETIME NOT NULL,
                banned_by INTEGER
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_messages_user_created
            ON messages (user_id, created_at DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_messages_created_at
            ON messages (created_at DESC)
            """
        )


def now_string() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_admin(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_ids


def is_banned_user(user_id: int) -> bool:
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM banned_users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return row is not None


def find_blocked_word(text: str, blacklist_words: list[str]) -> Optional[str]:
    normalized_text = text.casefold()
    for word in blacklist_words:
        if word.casefold() in normalized_text:
            return word
    return None


def create_message_record(user, text: str, is_blocked: int) -> tuple[int, str]:
    created_at = now_string()
    with get_db_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO messages (
                user_id,
                username,
                first_name,
                last_name,
                message_text,
                is_published,
                is_blocked,
                created_at,
                published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user.id,
                user.username,
                user.first_name or "",
                user.last_name,
                text,
                0,
                is_blocked,
                created_at,
                None,
            ),
        )
        return int(cursor.lastrowid), created_at


def mark_message_as_published(message_id: int) -> str:
    published_at = now_string()
    with get_db_connection() as connection:
        connection.execute(
            """
            UPDATE messages
            SET is_published = 1, published_at = ?
            WHERE id = ?
            """,
            (published_at, message_id),
        )
    return published_at


def get_latest_user_profile(user_id: int) -> dict[str, str]:
    with get_db_connection() as connection:
        row = connection.execute(
            """
            SELECT username, first_name, last_name
            FROM messages
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

    if row is None:
        return {"username": "", "first_name": "", "last_name": ""}

    return {
        "username": row["username"] or "",
        "first_name": row["first_name"] or "",
        "last_name": row["last_name"] or "",
    }


def upsert_banned_user(
    *,
    user_id: int,
    username: str,
    first_name: str,
    last_name: str,
    reason: str,
    banned_by: Optional[int],
) -> tuple[bool, str]:
    existing_profile = get_latest_user_profile(user_id)
    username = username or existing_profile["username"]
    first_name = first_name or existing_profile["first_name"]
    last_name = last_name or existing_profile["last_name"]
    banned_at = now_string()

    with get_db_connection() as connection:
        existing = connection.execute(
            "SELECT 1 FROM banned_users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO banned_users (
                user_id,
                username,
                first_name,
                last_name,
                reason,
                banned_at,
                banned_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                reason = excluded.reason,
                banned_at = excluded.banned_at,
                banned_by = excluded.banned_by
            """,
            (
                user_id,
                username,
                first_name,
                last_name,
                reason,
                banned_at,
                banned_by,
            ),
        )
    return existing is None, banned_at


def remove_banned_user(user_id: int) -> bool:
    with get_db_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM banned_users WHERE user_id = ?",
            (user_id,),
        )
    return cursor.rowcount > 0


def get_banned_users() -> list[sqlite3.Row]:
    with get_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT user_id, username, first_name, last_name, reason, banned_at, banned_by
            FROM banned_users
            ORDER BY banned_at DESC, user_id DESC
            """
        ).fetchall()
    return list(rows)


def get_user_posts(user_id: int, limit: int) -> list[sqlite3.Row]:
    with get_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, message_text, is_published, is_blocked, created_at, published_at
            FROM messages
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return list(rows)


def get_user_posts_and_stats(user_id: int, limit: int) -> tuple[dict[str, Optional[str]], list[sqlite3.Row]]:
    return get_user_stats(user_id), get_user_posts(user_id, limit)


def get_recent_users_with_messages(
    user_limit: int = 20,
    message_limit: int = 3,
) -> list[dict[str, object]]:
    with get_db_connection() as connection:
        user_rows = connection.execute(
            """
            SELECT
                user_id,
                COALESCE(MAX(username), '') AS username,
                COALESCE(MAX(first_name), '') AS first_name,
                COALESCE(MAX(last_name), '') AS last_name,
                MAX(id) AS latest_id
            FROM messages
            GROUP BY user_id
            ORDER BY latest_id DESC
            LIMIT ?
            """,
            (user_limit,),
        ).fetchall()

        results: list[dict[str, object]] = []
        for row in user_rows:
            message_rows = connection.execute(
                """
                SELECT message_text
                FROM messages
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (row["user_id"], message_limit),
            ).fetchall()
            results.append(
                {
                    "user_id": row["user_id"],
                    "username": row["username"],
                    "first_name": row["first_name"],
                    "last_name": row["last_name"],
                    "messages": [message_row["message_text"] for message_row in message_rows],
                }
            )

    return results


def get_user_stats(user_id: int) -> dict[str, Optional[str]]:
    with get_db_connection() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total_count,
                SUM(CASE WHEN is_published = 1 THEN 1 ELSE 0 END) AS published_count,
                SUM(CASE WHEN is_blocked = 1 THEN 1 ELSE 0 END) AS blocked_count,
                MAX(created_at) AS latest_created_at,
                MAX(published_at) AS latest_published_at
            FROM messages
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()

    return {
        "total_count": int(row["total_count"] or 0),
        "published_count": int(row["published_count"] or 0),
        "blocked_count": int(row["blocked_count"] or 0),
        "latest_created_at": row["latest_created_at"],
        "latest_published_at": row["latest_published_at"],
    }


def build_user_display_name(user) -> str:
    parts = [user.first_name or "", user.last_name or ""]
    full_name = " ".join(part for part in parts if part).strip()
    return full_name or "未提供姓名"


def build_user_display_name_from_row(row: sqlite3.Row) -> str:
    parts = [row["first_name"] or "", row["last_name"] or ""]
    full_name = " ".join(part for part in parts if part).strip()
    return full_name or "未提供姓名"


def build_channel_message(text: str, username: Optional[str]) -> str:
    return text


def build_admin_notification(
    *,
    user,
    text: str,
    status: str,
    created_at: str,
    record_id: Optional[int] = None,
    blocked_word: Optional[str] = None,
    published_at: Optional[str] = None,
    is_command: bool = False,
) -> str:
    username_text = f"@{user.username}" if user.username else "无用户名"
    safe_text = html.escape(text)
    lines = [
        "📨 <b>收到用户消息</b>",
        f"用户 ID：<code>{user.id}</code>",
        f"用户名：{html.escape(username_text)}",
        f"名称：{html.escape(build_user_display_name(user))}",
        f"时间：{created_at}",
        f"状态：{html.escape(status)}",
    ]

    if record_id is not None:
        lines.append(f"记录 ID：<code>{record_id}</code>")
    if blocked_word:
        lines.append(f"命中敏感词：<code>{html.escape(blocked_word)}</code>")
    if published_at:
        lines.append(f"发布时间：{published_at}")

    lines.extend(
        [
            f"类型：{'命令' if is_command else '文本'}",
            "",
            "<b>消息内容</b>",
            safe_text,
        ]
    )
    return "\n".join(lines)


def build_admin_action_keyboard(user_id: int, banned: bool) -> InlineKeyboardMarkup:
    if banned:
        buttons = [
            [InlineKeyboardButton("✅ 解封用户", callback_data=f"unban:{user_id}")]
        ]
    else:
        buttons = [
            [InlineKeyboardButton("🚫 封禁用户", callback_data=f"ban:{user_id}")]
        ]
    return InlineKeyboardMarkup(buttons)


def build_banned_user_text(row: sqlite3.Row) -> str:
    username = f"@{row['username']}" if row["username"] else "无用户名"
    return "\n".join(
        [
            "🚫 <b>黑名单用户</b>",
            f"用户 ID：<code>{row['user_id']}</code>",
            f"用户名：{html.escape(username)}",
            f"名称：{html.escape(build_user_display_name_from_row(row))}",
            f"原因：{html.escape(row['reason'] or '未填写')}",
            f"封禁时间：{row['banned_at']}",
        ]
    )


async def notify_admins(
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    text: str,
    *,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    for admin_id in settings.admin_ids:
        try:
            await telegram_api_call(
                lambda admin_id=admin_id: context.bot.send_message(
                    chat_id=admin_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                    disable_web_page_preview=True,
                ),
                operation=f"notify admin {admin_id}",
            )
        except Exception:
            logger.exception("Failed to notify admin_id=%s", admin_id)


def consume_cooldown(user_id: int, cooldown_seconds: int) -> float:
    if cooldown_seconds <= 0:
        return 0.0

    now = time.monotonic()
    last_time = MESSAGE_COOLDOWNS.get(user_id, 0.0)
    elapsed = now - last_time
    if elapsed < cooldown_seconds:
        return max(0.0, cooldown_seconds - elapsed)

    MESSAGE_COOLDOWNS[user_id] = now
    return 0.0


def parse_target_user_id(argument: str) -> int:
    value = argument.strip()
    if not re.fullmatch(r"\d+", value):
        raise ValueError("User ID must be numeric.")
    return int(value)


def build_user_commands() -> list[BotCommand]:
    return [
        BotCommand("start", "开始使用机器人"),
        BotCommand("help", "查看帮助"),
    ]


def build_admin_commands() -> list[BotCommand]:
    return [
        BotCommand("start", "开始使用机器人"),
        BotCommand("help", "查看帮助"),
        BotCommand("banned", "查看黑名单"),
        BotCommand("ban", "封禁用户"),
        BotCommand("unban", "解封用户"),
        BotCommand("searchuser", "按用户ID查询记录"),
        BotCommand("userlist", "查看最近用户列表"),
    ]


async def telegram_api_call(
    func: Callable[[], Awaitable[T]],
    *,
    operation: str,
    attempts: int = 3,
    initial_delay: float = 1.5,
) -> T:
    delay = initial_delay
    last_error: Optional[NetworkError] = None

    for attempt in range(1, attempts + 1):
        try:
            return await func()
        except NetworkError as exc:
            last_error = exc
            if attempt >= attempts:
                break

            logger.warning(
                "Telegram API call failed during %s (attempt %s/%s): %s. Retrying in %.1f seconds.",
                operation,
                attempt,
                attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
            delay *= 2

    if last_error is None:
        raise RuntimeError(f"Telegram API call failed unexpectedly during {operation}.")
    raise last_error


async def ensure_user_command_scope(
    application: Application,
    user_id: int,
    is_admin_user: bool,
) -> None:
    commands = build_admin_commands() if is_admin_user else build_user_commands()
    try:
        await telegram_api_call(
            lambda: application.bot.set_my_commands(
                commands,
                scope=BotCommandScopeChat(chat_id=user_id),
            ),
            operation=f"refresh command scope for user {user_id}",
        )
    except Exception:
        logger.exception(
            "Failed to refresh command scope for user_id=%s is_admin=%s",
            user_id,
            is_admin_user,
        )


async def setup_bot_commands(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]

    user_commands = build_user_commands()
    admin_commands = build_admin_commands()

    await telegram_api_call(
        lambda: application.bot.set_my_commands(
            user_commands,
            scope=BotCommandScopeDefault(),
        ),
        operation="set default bot commands",
    )

    for admin_id in settings.admin_ids:
        try:
            await telegram_api_call(
                lambda admin_id=admin_id: application.bot.set_my_commands(
                    admin_commands,
                    scope=BotCommandScopeChat(chat_id=admin_id),
                ),
                operation=f"set admin commands for {admin_id}",
            )
        except Exception:
            logger.exception("Failed to set scoped commands for admin_id=%s", admin_id)


async def validate_channel_access(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]
    try:
        chat = await telegram_api_call(
            lambda: application.bot.get_chat(settings.channel_id),
            operation=f"validate target channel {settings.channel_id}",
            attempts=4,
            initial_delay=2.0,
        )
        logger.info(
            "Verified target channel access: id=%s title=%s",
            settings.channel_id,
            getattr(chat, "title", None),
        )
    except BadRequest as exc:
        logger.error(
            "Cannot access target channel %s because the configuration is invalid or the bot lacks access: %s",
            settings.channel_id,
            exc,
        )
        raise RuntimeError(
            "Target channel is not accessible. Please check BOT.CHANNEL_ID, confirm the bot has been added to the channel, and make sure it can send messages."
        ) from exc
    except NetworkError as exc:
        logger.warning(
            "Skipping startup channel validation for %s because Telegram is temporarily unreachable: %s",
            settings.channel_id,
            exc,
        )
    except Exception as exc:
        logger.error(
            "Cannot access target channel %s. Check BOT.CHANNEL_ID, confirm the bot is already added to the channel and has permission to send messages. Error: %s",
            settings.channel_id,
            exc,
        )
        raise RuntimeError(
            "Target channel is not accessible. Please check BOT.CHANNEL_ID and bot permissions."
        ) from exc


async def post_init(application: Application) -> None:
    await setup_bot_commands(application)
    await validate_channel_access(application)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    settings: Settings = context.application.bot_data["settings"]

    if user is None or message is None:
        return

    await ensure_user_command_scope(
        context.application,
        user.id,
        is_admin(user.id, settings),
    )

    if is_admin(user.id, settings):
        await message.reply_text(
            "你当前是管理员账号。\n"
            "普通用户的文本消息会同步通知到这里。\n\n"
            "管理员命令：\n"
            "/banned - 查看黑名单\n"
            "/ban <用户ID> [原因] - 封禁用户\n"
            "/unban <用户ID> - 解封用户\n"
            "/searchuser <用户ID> [数量] - 查询该用户的投稿和统计\n"
            "/userlist - 查看最近 20 位用户和最近 3 条发言\n\n"
            "说明：机器人只接收文本投稿，表情符号可以，图片、视频、语音、文件不会入库。"
        )
        return

    channel_line = (
        f"投稿频道：{settings.channel_link}\n"
        if settings.channel_link
        else ""
    )
    await message.reply_text(
        "欢迎使用发言机器人。\n"
        f"{channel_line}"
        "直接给我发送文字，我会自动在频道发言。\n"
        "请不要攻击他人，不刷屏，不发布违法信息。\n"
        "我只接收文本信息，表情符号可以，图片、视频、语音、文件不会处理。\n\n"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start_command(update, context)


async def myposts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    settings: Settings = context.application.bot_data["settings"]
    if user is None or message is None:
        return
    if not is_admin(user.id, settings):
        await message.reply_text("不支持使用这个命令。")
        return

    limit = 10
    if context.args and context.args[0].isdigit():
        limit = max(1, min(int(context.args[0]), 20))

    posts = await asyncio.to_thread(get_user_posts, user.id, limit)
    if not posts:
        await message.reply_text("你还没有投稿记录。")
        return

    lines = [f"📋 <b>最近 {len(posts)} 条投稿记录</b>"]
    for row in posts:
        if row["is_published"]:
            status = "已发布"
        elif row["is_blocked"]:
            status = "已拦截"
        else:
            status = "未发布"

        preview = row["message_text"].replace("\n", " ").strip()
        if len(preview) > 40:
            preview = f"{preview[:40]}..."

        lines.extend(
            [
                "",
                f"#{row['id']} [{status}]",
                f"时间：{row['created_at']}",
                f"内容：{html.escape(preview)}",
            ]
        )

    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def mystats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    settings: Settings = context.application.bot_data["settings"]
    if user is None or message is None:
        return
    if not is_admin(user.id, settings):
        await message.reply_text("不支持使用这个命令。")
        return

    stats = await asyncio.to_thread(get_user_stats, user.id)
    await message.reply_text(
        "\n".join(
            [
                "📊 <b>个人统计</b>",
                f"总投稿数：{stats['total_count']}",
                f"已发布：{stats['published_count']}",
                f"已拦截：{stats['blocked_count']}",
                f"最近投稿时间：{stats['latest_created_at'] or '暂无'}",
                f"最近发布时间：{stats['latest_published_at'] or '暂无'}",
            ]
        ),
        parse_mode=ParseMode.HTML,
    )


async def searchuser_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    settings: Settings = context.application.bot_data["settings"]

    if user is None or message is None:
        return
    if not is_admin(user.id, settings):
        await message.reply_text("只有管理员可以使用这个命令。")
        return
    if not context.args:
        await message.reply_text("用法：/searchuser <用户ID> [数量]")
        return

    try:
        target_user_id = parse_target_user_id(context.args[0])
    except ValueError:
        await message.reply_text("用户 ID 必须是纯数字。")
        return

    limit = 10
    if len(context.args) > 1 and context.args[1].isdigit():
        limit = max(1, min(int(context.args[1]), 20))

    stats, posts = await asyncio.to_thread(get_user_posts_and_stats, target_user_id, limit)
    if stats["total_count"] == 0:
        await message.reply_text(f"用户 {target_user_id} 暂无投稿记录。")
        return

    lines = [
        f"🔎 <b>用户 {target_user_id} 的投稿与统计</b>",
        f"总投稿数：{stats['total_count']}",
        f"已发布：{stats['published_count']}",
        f"已拦截：{stats['blocked_count']}",
        f"最近投稿时间：{stats['latest_created_at'] or '暂无'}",
        f"最近发布时间：{stats['latest_published_at'] or '暂无'}",
    ]

    if posts:
        lines.append("")
        lines.append(f"<b>最近 {len(posts)} 条投稿</b>")
        for row in posts:
            if row["is_published"]:
                status = "已发布"
            elif row["is_blocked"]:
                status = "已拦截"
            else:
                status = "未发布"

            preview = row["message_text"].replace("\n", " ").strip()
            if len(preview) > 40:
                preview = f"{preview[:40]}..."

            lines.extend(
                [
                    "",
                    f"#{row['id']} [{status}]",
                    f"时间：{row['created_at']}",
                    f"内容：{html.escape(preview)}",
                ]
            )

    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def userlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    settings: Settings = context.application.bot_data["settings"]

    if user is None or message is None:
        return
    if not is_admin(user.id, settings):
        await message.reply_text("只有管理员可以使用这个命令。")
        return

    users = await asyncio.to_thread(get_recent_users_with_messages, 20, 3)
    if not users:
        await message.reply_text("当前还没有用户发言记录。")
        return

    lines = ["👥 <b>最近 20 位用户</b>"]
    for item in users:
        username = item["username"]
        if username:
            user_label = f"@{username}"
        else:
            user_label = f"用户{item['user_id']}"

        previews = []
        for raw_text in item["messages"]:
            preview = str(raw_text).replace("\n", " ").strip()
            if len(preview) > 20:
                preview = f"{preview[:20]}..."
            previews.append(html.escape(preview))

        joined_messages = "；".join(previews) if previews else "暂无消息"
        lines.append(f"{html.escape(user_label)} {joined_messages}")

    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    settings: Settings = context.application.bot_data["settings"]

    if user is None or message is None:
        return
    if not is_admin(user.id, settings):
        await message.reply_text("只有管理员可以使用这个命令。")
        return
    if not context.args:
        await message.reply_text("用法：/ban <用户ID> [原因]")
        return

    try:
        target_user_id = parse_target_user_id(context.args[0])
    except ValueError:
        await message.reply_text("用户 ID 必须是纯数字。")
        return

    if target_user_id in settings.admin_ids:
        await message.reply_text("不能封禁管理员账号。")
        return

    reason = " ".join(context.args[1:]).strip() or "触发风控自动封禁"
    profile = await asyncio.to_thread(get_latest_user_profile, target_user_id)
    created, banned_at = await asyncio.to_thread(
        upsert_banned_user,
        user_id=target_user_id,
        username=profile["username"],
        first_name=profile["first_name"],
        last_name=profile["last_name"],
        reason=reason,
        banned_by=user.id,
    )

    if created:
        await message.reply_text(
            f"已封禁用户 {target_user_id}\n原因：{reason}\n时间：{banned_at}"
        )
    else:
        await message.reply_text(
            f"已更新用户 {target_user_id} 的封禁信息\n原因：{reason}\n时间：{banned_at}"
        )


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    settings: Settings = context.application.bot_data["settings"]

    if user is None or message is None:
        return
    if not is_admin(user.id, settings):
        await message.reply_text("只有管理员可以使用这个命令。")
        return
    if not context.args:
        await message.reply_text("用法：/unban <用户ID>")
        return

    try:
        target_user_id = parse_target_user_id(context.args[0])
    except ValueError:
        await message.reply_text("用户 ID 必须是纯数字。")
        return

    removed = await asyncio.to_thread(remove_banned_user, target_user_id)
    if removed:
        await message.reply_text(f"已解封用户 {target_user_id}")
    else:
        await message.reply_text(f"用户 {target_user_id} 当前不在黑名单中。")


async def banned_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    settings: Settings = context.application.bot_data["settings"]

    if user is None or message is None:
        return
    if not is_admin(user.id, settings):
        await message.reply_text("只有管理员可以使用这个命令。")
        return

    banned_users = await asyncio.to_thread(get_banned_users)
    if not banned_users:
        await message.reply_text("当前黑名单为空。")
        return

    await message.reply_text(f"当前共有 {len(banned_users)} 位黑名单用户。")
    for row in banned_users:
        await message.reply_text(
            build_banned_user_text(row),
            parse_mode=ParseMode.HTML,
            reply_markup=build_admin_action_keyboard(row["user_id"], banned=True),
        )


async def handle_blacklist_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    user = update.effective_user
    settings: Settings = context.application.bot_data["settings"]

    if query is None or user is None:
        return

    if not is_admin(user.id, settings):
        await query.answer("只有管理员可以操作。", show_alert=True)
        return

    action, _, raw_user_id = (query.data or "").partition(":")
    if action not in {"ban", "unban"} or not raw_user_id.isdigit():
        await query.answer("无效操作。", show_alert=True)
        return

    target_user_id = int(raw_user_id)
    if action == "ban" and target_user_id in settings.admin_ids:
        await query.answer("不能封禁管理员账号。", show_alert=True)
        return

    if action == "ban":
        profile = await asyncio.to_thread(get_latest_user_profile, target_user_id)
        created, banned_at = await asyncio.to_thread(
            upsert_banned_user,
            user_id=target_user_id,
            username=profile["username"],
            first_name=profile["first_name"],
            last_name=profile["last_name"],
            reason="触发风控条件，已自动在消息通知中封禁",
            banned_by=user.id,
        )
        await query.answer("已封禁用户。" if created else "已更新封禁信息。", show_alert=True)
        await query.edit_message_reply_markup(
            reply_markup=build_admin_action_keyboard(target_user_id, banned=True)
        )
        await query.message.reply_text(
            f"用户 {target_user_id} 已加入黑名单\n时间：{banned_at}"
        )
        return

    removed = await asyncio.to_thread(remove_banned_user, target_user_id)
    if removed:
        await query.answer("已解封用户。", show_alert=True)
        await query.edit_message_reply_markup(
            reply_markup=build_admin_action_keyboard(target_user_id, banned=False)
        )
    else:
        await query.answer("该用户当前不在黑名单中。", show_alert=True)


async def handle_admin_text_only(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    user = update.effective_user
    message = update.effective_message
    settings: Settings = context.application.bot_data["settings"]

    if user is None or message is None:
        return
    if not is_admin(user.id, settings):
        return

    await message.reply_text("管理员发送的普通文字不会投稿到频道。")


async def handle_non_text_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    if message is None:
        return

    await message.reply_text(
        "机器人目前只接收文本信息投稿。\n"
        "表情符号可以正常发送，但图片、视频、语音、文件和贴纸不会处理。"
    )


async def handle_user_submission(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    user = update.effective_user
    message = update.effective_message
    settings: Settings = context.application.bot_data["settings"]

    if user is None or message is None or message.text is None:
        return
    if is_admin(user.id, settings):
        return

    text = message.text.strip()
    if not text:
        return

    created_at = now_string()

    if is_banned_user(user.id):
        record_id, created_at = await asyncio.to_thread(
            create_message_record,
            user,
            text,
            1,
        )
        await notify_admins(
            context,
            settings,
            build_admin_notification(
                user=user,
                text=text,
                status="已被封禁，拒绝投稿",
                created_at=created_at,
                record_id=record_id,
            ),
            reply_markup=build_admin_action_keyboard(user.id, banned=True),
        )
        await message.reply_text("❌ 你已被封禁，当前无法继续投稿。")
        return

    remaining = consume_cooldown(user.id, settings.user_message_cooldown)
    if remaining > 0:
        record_id, created_at = await asyncio.to_thread(
            create_message_record,
            user,
            text,
            1,
        )
        wait_seconds = max(1, int(remaining + 0.999))
        await notify_admins(
            context,
            settings,
            build_admin_notification(
                user=user,
                text=text,
                status=f"发送过快，需等待 {wait_seconds} 秒",
                created_at=created_at,
                record_id=record_id,
            ),
            reply_markup=build_admin_action_keyboard(user.id, banned=False),
        )
        await message.reply_text(f"⏳ 发送过于频繁，请在 {wait_seconds} 秒后再试。")
        return

    blocked_word = find_blocked_word(text, settings.blacklist_words)
    record_id, created_at = await asyncio.to_thread(
        create_message_record,
        user,
        text,
        1 if blocked_word else 0,
    )

    if blocked_word:
        await notify_admins(
            context,
            settings,
            build_admin_notification(
                user=user,
                text=text,
                status="命中敏感词，已拦截",
                created_at=created_at,
                record_id=record_id,
                blocked_word=blocked_word,
            ),
            reply_markup=build_admin_action_keyboard(user.id, banned=False),
        )
        await message.reply_text("❌ 消息包含违规内容，无法发布")
        return

    try:
        await telegram_api_call(
            lambda: context.bot.send_message(
                chat_id=settings.channel_id,
                text=build_channel_message(text, user.username),
            ),
            operation=f"publish submission {record_id}",
        )
        published_at = await asyncio.to_thread(mark_message_as_published, record_id)
        await notify_admins(
            context,
            settings,
            build_admin_notification(
                user=user,
                text=text,
                status="已发布",
                created_at=created_at,
                record_id=record_id,
                published_at=published_at,
            ),
            reply_markup=build_admin_action_keyboard(user.id, banned=False),
        )
        await message.reply_text("✅ 发布成功")
    except Exception:
        logger.exception(
            "Failed to publish submission id=%s user_id=%s",
            record_id,
            user.id,
        )
        await notify_admins(
            context,
            settings,
            build_admin_notification(
                user=user,
                text=text,
                status="发布失败",
                created_at=created_at,
                record_id=record_id,
            ),
            reply_markup=build_admin_action_keyboard(user.id, banned=False),
        )
        await message.reply_text("❌ 发布失败，请稍后重试")


def main() -> None:
    ensure_runtime_dirs()
    setup_logging()

    settings = load_settings()
    init_db()

    application = (
        Application.builder()
        .token(settings.token)
        .request(build_httpx_request(read_timeout=20.0))
        .get_updates_request(build_httpx_request(read_timeout=35.0))
        .post_init(post_init)
        .build()
    )
    application.bot_data["settings"] = settings

    application.add_handler(CallbackQueryHandler(handle_blacklist_callback, pattern=r"^(ban|unban):\d+$"))
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("myposts", myposts_command))
    application.add_handler(CommandHandler("mystats", mystats_command))
    application.add_handler(CommandHandler("searchuser", searchuser_command))
    application.add_handler(CommandHandler("userlist", userlist_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("unban", unban_command))
    application.add_handler(CommandHandler("banned", banned_command))
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            handle_admin_text_only,
        ),
        group=0,
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            handle_user_submission,
        ),
        group=1,
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.TEXT,
            handle_non_text_message,
        ),
        group=2,
    )

    logger.info(
        "Bot is starting with %s admin(s) configured",
        len(settings.admin_ids),
    )
    application.run_polling(bootstrap_retries=5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception:
        logger.exception("Bot stopped due to an unexpected error")
        raise
