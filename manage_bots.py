#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace

from delete_bots import format_bot_label, load_all_bots
from setup_bots import (
    BASE_DIR,
    MANIFEST_PATH,
    BotDefinition,
    render_config,
    save_manifest,
    validate_admin_ids,
    validate_channel_id,
    validate_channel_link,
    validate_cooldown,
    validate_token,
)

def ask_choice(prompt: str, valid_choices: set[str], default: str) -> str:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        choice = raw or default
        if choice in valid_choices:
            return choice
        print("输入无效，请重新选择。")


def ask_index(items: list[BotDefinition], prompt: str) -> int:
    while True:
        raw = input(f"{prompt}: ").strip()
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(items):
                return index - 1
        print("序号无效，请重新输入。")


def is_managed_config(config_filename: str) -> bool:
    return config_filename.startswith("config") and config_filename.endswith(".ini") and config_filename != "config.ini"


def display_value(field_name: str, value: str) -> str:
    return value or "(空)"


def ask_update_value(
    *,
    field_name: str,
    current_value: str,
    validator,
    required: bool,
) -> str:
    choice = ask_choice(
        f"{field_name} 当前值：{display_value(field_name, current_value)}，1. 保持默认  2. 更改",
        {"1", "2"},
        "1",
    )
    if choice == "1":
        return current_value

    while True:
        print(f"{field_name} 原值：{display_value(field_name, current_value)}")
        prompt = f"请输入新的 {field_name}"
        if not required:
            prompt += "（直接回车表示清空）"
        prompt += ": "

        new_value = input(prompt).strip()
        if not new_value and not required:
            return ""
        if not new_value and required:
            print(f"{field_name} 不能为空。")
            continue
        if validator is not None and not validator(new_value):
            print(f"{field_name} 格式无效，请重新输入。")
            continue
        return new_value


def save_bot_config(bot: BotDefinition) -> None:
    config_path = BASE_DIR / bot.config_filename
    config_path.write_text(render_config(bot), encoding="utf-8")


def save_managed_manifest(bots: list[BotDefinition]) -> None:
    managed_bots = [bot for bot in bots if is_managed_config(bot.config_filename)]
    if managed_bots:
        save_manifest(managed_bots)
    elif MANIFEST_PATH.exists():
        MANIFEST_PATH.unlink()


def update_bot_definition(bot: BotDefinition) -> BotDefinition:
    token = ask_update_value(
        field_name="TOKEN",
        current_value=bot.token,
        validator=validate_token,
        required=True,
    )
    channel_id = ask_update_value(
        field_name="CHANNEL_ID",
        current_value=bot.channel_id,
        validator=validate_channel_id,
        required=True,
    )
    channel_link = ask_update_value(
        field_name="CHANNEL_LINK",
        current_value=bot.channel_link,
        validator=validate_channel_link,
        required=True,
    )
    admin_ids = ask_update_value(
        field_name="ADMIN_IDS",
        current_value=bot.admin_ids,
        validator=validate_admin_ids,
        required=False,
    )
    cooldown = ask_update_value(
        field_name="USER_MESSAGE_COOLDOWN",
        current_value=bot.user_message_cooldown,
        validator=validate_cooldown,
        required=True,
    )
    blacklist = ask_update_value(
        field_name="BLACKLIST",
        current_value=bot.blacklist,
        validator=None,
        required=False,
    )

    return replace(
        bot,
        token=token,
        channel_id=channel_id,
        channel_link=channel_link,
        admin_ids=admin_ids,
        user_message_cooldown=cooldown,
        blacklist=blacklist,
    )


def run_command(command: list[str], description: str) -> bool:
    print(f"正在执行：{description}")
    try:
        subprocess.run(command, cwd=BASE_DIR, check=True)
        return True
    except FileNotFoundError:
        print("未找到 docker 或 python3，请确认 VPS 环境已安装。")
        return False
    except subprocess.CalledProcessError as exc:
        print(f"{description} 失败，退出码：{exc.returncode}")
        return False


def run_python_script(script_name: str, description: str) -> None:
    run_command([sys.executable, str(BASE_DIR / script_name)], description)


def get_service_states() -> dict[str, str]:
    command = [
        "docker",
        "compose",
        "ps",
        "-a",
        "--format",
        "{{.Service}}|{{.State}}",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=BASE_DIR,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return {}
    except subprocess.CalledProcessError:
        return {}

    states: dict[str, str] = {}
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line or "|" not in line:
            continue
        service_name, state = line.split("|", 1)
        states[service_name.strip()] = state.strip()
    return states


def normalize_state(state: str) -> str:
    if not state:
        return "未创建"
    lowered = state.casefold()
    if "running" in lowered:
        return "启动中"
    if "exited" in lowered or "dead" in lowered or "stopped" in lowered:
        return "已停止"
    if "created" in lowered:
        return "已创建"
    return state


def print_bot_statuses(bots: list[BotDefinition]) -> None:
    states = get_service_states()
    print("")
    print("当前机器人容器状态：")
    for index, bot in enumerate(bots, start=1):
        status = normalize_state(states.get(bot.service_name, ""))
        print(f"{index}. {format_bot_label(bot)} - {status}")


def modify_bot_menu() -> None:
    bots = load_all_bots()
    if not bots:
        print("当前没有可修改的机器人配置。")
        return

    print_bot_statuses(bots)
    selected_index = ask_index(bots, "请选择要修改的机器人序号")
    selected_bot = bots[selected_index]

    updated_bot = update_bot_definition(selected_bot)
    save_bot_config(updated_bot)

    bots[selected_index] = updated_bot
    save_managed_manifest(bots)
    print(f"已更新配置文件：{updated_bot.config_filename}")

    run_command(
        [
            "docker",
            "compose",
            "up",
            "-d",
            "--build",
            updated_bot.service_name,
        ],
        f"重建并启动容器 {updated_bot.service_name}",
    )


def start_all_containers() -> None:
    run_command(
        ["docker", "compose", "up", "-d", "--build", "--remove-orphans"],
        "启动全部机器人容器并同步最新代码",
    )


def stop_all_containers() -> None:
    run_command(
        ["docker", "compose", "stop"],
        "停止全部机器人容器",
    )


def manage_single_container() -> None:
    bots = load_all_bots()
    if not bots:
        print("当前没有可管理的机器人容器。")
        return

    print_bot_statuses(bots)
    selected_index = ask_index(bots, "请选择要操作的机器人序号")
    selected_bot = bots[selected_index]

    action = ask_choice(
        f"请选择对 {selected_bot.service_name} 的操作：1. 启动  2. 停止  3. 返回",
        {"1", "2", "3"},
        "3",
    )
    if action == "3":
        return
    if action == "1":
        run_command(
            [
                "docker",
                "compose",
                "up",
                "-d",
                "--build",
                selected_bot.service_name,
            ],
            f"启动容器 {selected_bot.service_name} 并同步最新代码",
        )
        return

    run_command(
        ["docker", "compose", "stop", selected_bot.service_name],
        f"停止容器 {selected_bot.service_name}",
    )


def container_management_menu() -> None:
    while True:
        bots = load_all_bots()
        if bots:
            print_bot_statuses(bots)
        else:
            print("当前没有可管理的机器人容器。")

        choice = ask_choice(
            "机器人容器启动管理：1. 启动所有容器  2. 停止所有容器  3. 单独管理容器  4. 返回",
            {"1", "2", "3", "4"},
            "4",
        )
        if choice == "1":
            start_all_containers()
            continue
        if choice == "2":
            stop_all_containers()
            continue
        if choice == "3":
            manage_single_container()
            continue
        return


def main() -> None:
    print("机器人容器管理脚本")

    while True:
        choice = ask_choice(
            "请选择功能：1. 添加机器人容器  2. 删除机器人容器  3. 修改机器人容器  4. 机器人容器启动管理  5. 退出",
            {"1", "2", "3", "4", "5"},
            "5",
        )
        if choice == "1":
            run_python_script("setup_bots.py", "添加机器人容器配置")
            continue
        if choice == "2":
            run_python_script("delete_bots.py", "删除机器人容器配置")
            continue
        if choice == "3":
            modify_bot_menu()
            continue
        if choice == "4":
            container_management_menu()
            continue
        print("已退出脚本。")
        return


if __name__ == "__main__":
    main()
