#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import configparser
import json
import sqlite3
import re
from dataclasses import asdict, dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DOCKER_COMPOSE_PATH = BASE_DIR / "docker-compose.yml"
CONFIG_EXAMPLE_PATH = BASE_DIR / "config.ini.example"
MANIFEST_PATH = BASE_DIR / ".msb-bots.json"


@dataclass
class BotDefinition:
    index: int
    service_name: str
    container_name: str
    config_filename: str
    data_dir: str
    logs_dir: str
    token: str
    channel_id: str
    channel_link: str
    admin_ids: str
    user_message_cooldown: str
    blacklist: str


def ask_choice(prompt: str, valid_choices: set[str], default: str) -> str:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        choice = raw or default
        if choice in valid_choices:
            return choice
        print("输入无效，请重新选择。")


def ask_required(
    prompt: str,
    validator=None,
    error_message: str = "该项不能为空。",
) -> str:
    while True:
        value = input(f"{prompt}: ").strip()
        if not value:
            print(error_message)
            continue
        if validator is not None and not validator(value):
            print(error_message)
            continue
        return value


def ask_optional(
    prompt: str,
    default: str,
    validator=None,
    error_message: str = "输入格式无效。",
) -> str:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        value = raw or default
        if validator is not None and value and not validator(value):
            print(error_message)
            continue
        return value


def validate_channel_id(value: str) -> bool:
    return bool(re.fullmatch(r"-?\d+", value))


def validate_token(value: str) -> bool:
    return len(value) >= 10 and ":" in value and " " not in value


def validate_channel_link(value: str) -> bool:
    return bool(re.fullmatch(r"@[A-Za-z0-9_]{4,}", value))


def validate_admin_ids(value: str) -> bool:
    if not value:
        return True
    parts = [item.strip() for item in value.split(",")]
    return all(re.fullmatch(r"\d+", item) for item in parts if item)


def validate_cooldown(value: str) -> bool:
    return bool(re.fullmatch(r"\d+", value))


def validate_bot_name(value: str) -> bool:
    return bool(re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,49}", value))


def load_manifest() -> list[BotDefinition]:
    if not MANIFEST_PATH.exists():
        discovered_bots = discover_existing_bots()
        if discovered_bots:
            save_manifest(discovered_bots)
        return discovered_bots

    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    bots: list[BotDefinition] = []
    for item in raw:
        item.setdefault("token", "")
        bots.append(BotDefinition(**item))
    return bots


def save_manifest(bots: list[BotDefinition]) -> None:
    MANIFEST_PATH.write_text(
        json.dumps([asdict(bot) for bot in bots], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_generated_services() -> dict[str, str]:
    if not DOCKER_COMPOSE_PATH.exists():
        return {}

    service_for_config: dict[str, str] = {}
    current_service = ""

    for raw_line in DOCKER_COMPOSE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        service_match = re.fullmatch(r"  ([A-Za-z][A-Za-z0-9_-]{0,49}):", line)
        if service_match:
            current_service = service_match.group(1)
            continue

        config_match = re.search(r"\./(config\d+\.ini):/app/\1:ro", line)
        if current_service and config_match:
            service_for_config[config_match.group(1)] = current_service

    return service_for_config


def discover_existing_bots() -> list[BotDefinition]:
    service_mapping = parse_generated_services()
    bots: list[BotDefinition] = []

    for config_path in sorted(BASE_DIR.glob("config[0-9]*.ini")):
        match = re.fullmatch(r"config(\d+)\.ini", config_path.name)
        if not match:
            continue

        index = int(match.group(1))
        service_name = service_mapping.get(config_path.name, f"bot{index}")

        parser = configparser.ConfigParser()
        parser.read(config_path, encoding="utf-8")

        token = parser.get("BOT", "TOKEN", fallback="").strip()
        channel_id = parser.get("BOT", "CHANNEL_ID", fallback="").strip()
        channel_link = parser.get("BOT", "CHANNEL_LINK", fallback="").strip()
        admin_ids = parser.get("BOT", "ADMIN_IDS", fallback="").strip()
        user_message_cooldown = parser.get(
            "BOT",
            "USER_MESSAGE_COOLDOWN",
            fallback="30",
        ).strip()
        blacklist = parser.get("FILTER", "BLACKLIST", fallback="").strip()

        bots.append(
            BotDefinition(
                index=index,
                service_name=service_name,
                container_name=f"my-simple-bot-{service_name}",
                config_filename=config_path.name,
                data_dir=f"./data/{service_name}",
                logs_dir=f"./logs/{service_name}",
                token=token,
                channel_id=channel_id,
                channel_link=channel_link,
                admin_ids=admin_ids,
                user_message_cooldown=user_message_cooldown or "30",
                blacklist=blacklist,
            )
        )

    return bots


def build_bot_definition(existing_names: set[str], index: int) -> BotDefinition:
    print("")
    print(f"开始配置第 {index} 个机器人")

    name_choice = ask_choice(
        "机器人名称选项：1. 使用默认名称  2. 自定义名称",
        {"1", "2"},
        "1",
    )

    default_name = f"bot{index}"
    if name_choice == "1":
        service_name = default_name
    else:
        while True:
            candidate = ask_required(
                "请输入机器人名称",
                validator=validate_bot_name,
                error_message="名称只能使用字母、数字、-、_，且必须以字母开头。",
            )
            if candidate in existing_names:
                print("该机器人名称已存在，请换一个。")
                continue
            service_name = candidate
            break

    if service_name in existing_names:
        raise ValueError(f"机器人名称重复: {service_name}")

    token = ask_required(
        "请输入 TOKEN（必填）",
        validator=validate_token,
        error_message="TOKEN 格式无效，通常应类似 123456:ABCDEF... 。",
    )
    channel_id = ask_required(
        "请输入 CHANNEL_ID（必填）",
        validator=validate_channel_id,
        error_message="CHANNEL_ID 必须是数字，推荐格式如 -100xxxxxxxxxx。",
    )
    channel_link = ask_required(
        "请输入 CHANNEL_LINK（必填）",
        validator=validate_channel_link,
        error_message="CHANNEL_LINK 必须是 @channel 这种格式。",
    )
    admin_ids = ask_optional(
        "请输入 ADMIN_IDS（选填，多个用英文逗号分隔）",
        "",
        validator=validate_admin_ids,
        error_message="ADMIN_IDS 必须是数字，多个用英文逗号分隔。",
    )
    cooldown = ask_optional(
        "请输入 USER_MESSAGE_COOLDOWN（选填）",
        "30",
        validator=validate_cooldown,
        error_message="USER_MESSAGE_COOLDOWN 必须是整数。",
    )
    blacklist = ask_optional(
        "请输入 BLACKLIST（选填，多个用英文逗号分隔）",
        "",
    )

    return BotDefinition(
        index=index,
        service_name=service_name,
        container_name=f"my-simple-bot-{service_name}",
        config_filename=f"config{index}.ini",
        data_dir=f"./data/{service_name}",
        logs_dir=f"./logs/{service_name}",
        token=token,
        channel_id=channel_id,
        channel_link=channel_link,
        admin_ids=admin_ids,
        user_message_cooldown=cooldown,
        blacklist=blacklist,
    )


def render_config(bot: BotDefinition) -> str:
    return (
        "[BOT]\n"
        f"TOKEN = {bot.token}\n"
        f"CHANNEL_ID = {bot.channel_id}\n"
        f"CHANNEL_LINK = {bot.channel_link}\n"
        f"ADMIN_IDS = {bot.admin_ids}\n"
        f"USER_MESSAGE_COOLDOWN = {bot.user_message_cooldown}\n"
        "\n"
        "[FILTER]\n"
        f"BLACKLIST = {bot.blacklist}\n"
    )


def render_service(bot: BotDefinition) -> list[str]:
    return [
        f"  {bot.service_name}:",
        "    build:",
        "      context: .",
        "      dockerfile: Dockerfile",
        f"    container_name: {bot.container_name}",
        "    init: true",
        "    restart: unless-stopped",
        "    read_only: true",
        "    tmpfs:",
        "      - /tmp",
        "    environment:",
        "      TZ: Asia/Shanghai",
        f"      CONFIG_FILE: /app/{bot.config_filename}",
        "    volumes:",
        f"      - ./{bot.config_filename}:/app/{bot.config_filename}:ro",
        f"      - {bot.data_dir}:/app/data",
        f"      - {bot.logs_dir}:/app/logs",
        "    security_opt:",
        "      - no-new-privileges:true",
        "    cap_drop:",
        "      - ALL",
        "    pids_limit: 128",
        "    mem_limit: 256m",
        "    cpus: 1.0",
        "    logging:",
        "      driver: json-file",
        "      options:",
        '        max-size: "10m"',
        '        max-file: "3"',
    ]


def render_docker_compose(bots: list[BotDefinition]) -> str:
    lines = [
        "# This file is generated by setup_bots.py",
        "services:",
    ]
    for bot in bots:
        lines.extend(render_service(bot))
    return "\n".join(lines) + "\n"


def initialize_database_schema(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
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
        connection.commit()
    finally:
        connection.close()


def ensure_runtime_directories_and_databases(bots: list[BotDefinition]) -> list[str]:
    actions: list[str] = []
    (BASE_DIR / "data").mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "logs").mkdir(parents=True, exist_ok=True)

    for bot in bots:
        data_path = BASE_DIR / "data" / bot.service_name
        logs_path = BASE_DIR / "logs" / bot.service_name
        data_path.mkdir(parents=True, exist_ok=True)
        logs_path.mkdir(parents=True, exist_ok=True)

        db_path = data_path / "messages.db"
        if db_path.exists():
            actions.append(f"{bot.service_name}: 保留已有数据库 {db_path.name}")
            continue

        initialize_database_schema(db_path)
        actions.append(f"{bot.service_name}: 已创建数据库并初始化表结构 {db_path.name}")

    return actions


def write_outputs(bots: list[BotDefinition]) -> None:
    for bot in bots:
        config_path = BASE_DIR / bot.config_filename
        config_path.write_text(render_config(bot), encoding="utf-8")

    DOCKER_COMPOSE_PATH.write_text(render_docker_compose(bots), encoding="utf-8")
    save_manifest(bots)


def print_summary(bots: list[BotDefinition], db_actions: list[str]) -> None:
    print("")
    print("已生成或更新以下文件：")
    for bot in bots:
        print(f"- {bot.config_filename} -> 服务名 {bot.service_name}")
    print(f"- {DOCKER_COMPOSE_PATH.name}")
    print(f"- {MANIFEST_PATH.name}")

    if db_actions:
        print("")
        print("数据库处理结果：")
        for line in db_actions:
            print(f"- {line}")

    print("")
    print("后续可执行：")
    print("sudo docker compose up -d --build")
    print("")
    print("后续再次启动配置脚本：")
    print("msb")


def main() -> None:
    print("机器人批量初始化脚本")
    print("该脚本会生成多个 configN.ini，并重写 docker-compose.yml。")

    if not CONFIG_EXAMPLE_PATH.exists():
        print("未找到 config.ini.example，无法继续。")
        raise SystemExit(1)

    bots = load_manifest()
    existing_names = {bot.service_name for bot in bots}
    next_index = max((bot.index for bot in bots), default=0) + 1

    if bots:
        print(f"当前已存在 {len(bots)} 个机器人配置。")
    else:
        print("当前还没有机器人配置。")

    first_choice = ask_choice(
        "是否增加机器人？1. 保持默认，结束脚本  2. 增加",
        {"1", "2"},
        "1",
    )
    if first_choice == "1":
        print("已结束脚本，未做任何修改。")
        return

    while True:
        bot = build_bot_definition(existing_names, next_index)
        bots.append(bot)
        existing_names.add(bot.service_name)

        continue_choice = ask_choice(
            "是否继续增加机器人？1. 默认不增加，结束循环  2. 增加",
            {"1", "2"},
            "1",
        )
        if continue_choice == "1":
            break

        next_index += 1

    db_actions = ensure_runtime_directories_and_databases(bots)
    write_outputs(bots)
    print_summary(bots, db_actions)


if __name__ == "__main__":
    main()
