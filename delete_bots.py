#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import configparser
import re
import shutil
from pathlib import Path
from typing import Optional

from setup_bots import (
    BASE_DIR,
    DOCKER_COMPOSE_PATH,
    MANIFEST_PATH,
    BotDefinition,
    load_manifest,
    render_docker_compose,
    save_manifest,
)


MANAGED_CONFIG_PATTERN = re.compile(r"config\d+\.ini")
SERVICE_PATTERN = re.compile(r"  ([A-Za-z][A-Za-z0-9_-]{0,49}):")
CONTAINER_PATTERN = re.compile(r"\s*container_name:\s+(.+)")
CONFIG_ENV_PATTERN = re.compile(r"\s*CONFIG_FILE:\s+/app/(.+)")


def ask_choice(prompt: str, valid_choices: set[str], default: str) -> str:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        choice = raw or default
        if choice in valid_choices:
            return choice
        print("输入无效，请重新选择。")


def ask_index(bots: list[BotDefinition]) -> int:
    while True:
        raw = input("请选择要删除的机器人序号: ").strip()
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(bots):
                return index - 1
        print("序号无效，请重新输入。")


def load_bot_config(config_filename: str) -> tuple[str, str, str, str, str, str]:
    config_path = BASE_DIR / config_filename
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")

    return (
        parser.get("BOT", "TOKEN", fallback="").strip(),
        parser.get("BOT", "CHANNEL_ID", fallback="").strip(),
        parser.get("BOT", "CHANNEL_LINK", fallback="").strip(),
        parser.get("BOT", "ADMIN_IDS", fallback="").strip(),
        parser.get("BOT", "USER_MESSAGE_COOLDOWN", fallback="30").strip() or "30",
        parser.get("FILTER", "BLACKLIST", fallback="").strip(),
    )


def discover_default_bot() -> Optional[BotDefinition]:
    config_path = BASE_DIR / "config.ini"
    if not DOCKER_COMPOSE_PATH.exists() or not config_path.exists():
        return None

    current_service = ""
    current_container_name = ""
    for raw_line in DOCKER_COMPOSE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()

        service_match = SERVICE_PATTERN.fullmatch(line)
        if service_match:
            current_service = service_match.group(1)
            current_container_name = ""
            continue

        container_match = CONTAINER_PATTERN.fullmatch(line)
        if current_service and container_match:
            current_container_name = container_match.group(1).strip()
            continue

        config_match = CONFIG_ENV_PATTERN.fullmatch(line)
        if current_service and config_match:
            config_filename = Path(config_match.group(1).strip()).name
            if config_filename != "config.ini":
                continue

            token, channel_id, channel_link, admin_ids, cooldown, blacklist = load_bot_config(
                config_filename
            )
            return BotDefinition(
                index=0,
                service_name=current_service,
                container_name=current_container_name or f"my-simple-bot-{current_service}",
                config_filename=config_filename,
                data_dir="./data",
                logs_dir="./logs",
                token=token,
                channel_id=channel_id,
                channel_link=channel_link,
                admin_ids=admin_ids,
                user_message_cooldown=cooldown,
                blacklist=blacklist,
            )

    return None


def load_all_bots() -> list[BotDefinition]:
    bots = load_manifest()
    default_bot = discover_default_bot()

    if (
        default_bot is not None
        and all(
            bot.service_name != default_bot.service_name
            and bot.config_filename != default_bot.config_filename
            for bot in bots
        )
    ):
        bots.insert(0, default_bot)

    return bots


def resolve_project_path(path_text: str) -> Path:
    raw = path_text.strip()
    if raw.startswith("./"):
        path = BASE_DIR / raw[2:]
    else:
        path = BASE_DIR / raw

    resolved = path.resolve()
    if not resolved.is_relative_to(BASE_DIR.resolve()):
        raise RuntimeError(f"拒绝处理项目目录之外的路径: {resolved}")
    return resolved


def delete_file_if_exists(path: Path) -> None:
    if path.exists() and path.is_file():
        path.unlink()


def delete_directory_if_exists(path: Path) -> None:
    if path.exists() and path.is_dir():
        shutil.rmtree(path)


def delete_config_file(bot: BotDefinition) -> None:
    delete_file_if_exists((BASE_DIR / bot.config_filename).resolve())


def delete_data_artifacts(bot: BotDefinition) -> None:
    data_root = (BASE_DIR / "data").resolve()
    data_path = resolve_project_path(bot.data_dir)

    if data_path == data_root:
        for filename in ("messages.db", "messages.db-shm", "messages.db-wal", "messages.db-journal"):
            delete_file_if_exists(data_path / filename)
        return

    delete_directory_if_exists(data_path)


def delete_log_artifacts(bot: BotDefinition) -> None:
    logs_root = (BASE_DIR / "logs").resolve()
    logs_path = resolve_project_path(bot.logs_dir)

    if logs_path == logs_root:
        for log_path in logs_path.glob("bot.log*"):
            if log_path.is_file():
                log_path.unlink()
        return

    delete_directory_if_exists(logs_path)


def write_outputs(bots: list[BotDefinition]) -> None:
    DOCKER_COMPOSE_PATH.write_text(render_docker_compose(bots), encoding="utf-8")

    managed_bots = [
        bot for bot in bots if MANAGED_CONFIG_PATTERN.fullmatch(bot.config_filename)
    ]
    if managed_bots:
        save_manifest(managed_bots)
    elif MANIFEST_PATH.exists():
        MANIFEST_PATH.unlink()


def format_bot_label(bot: BotDefinition) -> str:
    return f"{bot.service_name} ({bot.config_filename})"


def delete_bot(bot: BotDefinition, bots: list[BotDefinition]) -> None:
    delete_config_file(bot)
    delete_data_artifacts(bot)
    delete_log_artifacts(bot)

    remaining_bots = [
        item
        for item in bots
        if not (
            item.service_name == bot.service_name
            and item.config_filename == bot.config_filename
        )
    ]
    write_outputs(remaining_bots)


def main() -> None:
    print("机器人删除脚本")
    print("此脚本会删除所选机器人的配置、数据库、日志目录，并从 docker-compose.yml 中移除对应服务。")

    while True:
        bots = load_all_bots()
        if not bots:
            print("当前没有可删除的机器人配置。")
            return

        print("")
        print("当前机器人列表：")
        for idx, bot in enumerate(bots, start=1):
            print(f"{idx}. {format_bot_label(bot)}")

        target_index = ask_index(bots)
        target_bot = bots[target_index]

        confirm_delete = ask_choice(
            f"是否删除机器人 {target_bot.service_name}？1. 否  2. 是",
            {"1", "2"},
            "1",
        )
        if confirm_delete == "1":
            continue_delete = ask_choice(
                "是否继续删除机器人？1. 否  2. 是",
                {"1", "2"},
                "1",
            )
            if continue_delete == "1":
                print("已退出脚本。")
                return
            continue

        warning_confirm = ask_choice(
            "警告：此操作会删除对应机器人配置及数据库，是否继续？1. 否  2. 是",
            {"1", "2"},
            "1",
        )
        if warning_confirm == "1":
            continue_delete = ask_choice(
                "是否继续删除机器人？1. 否  2. 是",
                {"1", "2"},
                "1",
            )
            if continue_delete == "1":
                print("已退出脚本。")
                return
            continue

        delete_bot(target_bot, bots)
        print(f"已删除机器人：{target_bot.service_name}")
        print("如对应容器仍在运行，请执行 sudo docker compose up -d --remove-orphans 同步清理孤儿容器。")

        continue_delete = ask_choice(
            "是否继续删除机器人？1. 否  2. 是",
            {"1", "2"},
            "1",
        )
        if continue_delete == "1":
            print("已退出脚本。")
            return


if __name__ == "__main__":
    main()
