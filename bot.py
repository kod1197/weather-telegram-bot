#!/usr/bin/env python3
"""Telegram bot wrapper for weather.py."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from weather import (
    WeatherError,
    find_city,
    format_daily_summary,
    format_weather,
    get_today_weather,
    get_time_based_greeting,
    get_weather,
)


API_BASE_URL = "https://api.telegram.org"
BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
BOT_DB_ENV = "WEATHER_BOT_DB"
ADMIN_USERNAMES_ENV = "ADMIN_USERNAMES"
LONG_POLL_TIMEOUT = 30
DEFAULT_DB_PATH = os.path.join("data", "weather-bot.sqlite3")
DEFAULT_ADMIN_USERNAMES = "kod1197"
PENDING_SET_CITY = "set_default_city"
PENDING_SET_TIME = "set_notification_time"
PENDING_ADMIN_BAN = "admin_ban_user"
PENDING_ADMIN_UNBAN = "admin_unban_user"
BUTTON_WEATHER = "🌤 Погода"
BUTTON_TODAY = "📅 Сегодня"
BUTTON_SET_CITY = "🏙 Настроить город"
BUTTON_MY_CITY = "📍 Мой город"
BUTTON_SET_TIME = "⏰ Настроить рассылку"
BUTTON_DISABLE_NOTIFICATIONS = "🔕 Отключить рассылку"
BUTTON_HELP = "ℹ️ Помощь"
BUTTON_ADMIN_MENU = "🛠 Админка"
BUTTON_ADMIN_METRICS = "📊 Метрики"
BUTTON_ADMIN_USERS = "👥 Пользователи"
BUTTON_ADMIN_BAN = "⛔ Забанить"
BUTTON_ADMIN_UNBAN = "✅ Разбанить"
BUTTON_ADMIN_BANS = "🚫 Баны"
BUTTON_MAIN_MENU = "↩️ Основное меню"
ADMIN_COMMANDS = {
    "/admin",
    "/admin_metrics",
    "/admin_users",
    "/admin_ban",
    "/admin_unban",
    "/admin_bans",
    "/ban",
    "/unban",
    "/bans",
}
ADMIN_BUTTONS = {
    BUTTON_ADMIN_MENU,
    BUTTON_ADMIN_METRICS,
    BUTTON_ADMIN_USERS,
    BUTTON_ADMIN_BAN,
    BUTTON_ADMIN_UNBAN,
    BUTTON_ADMIN_BANS,
}
BOT_COMMANDS = [
    {"command": "weather", "description": "Погода для города по умолчанию"},
    {"command": "today", "description": "Сводка на день"},
    {"command": "setcity", "description": "Сохранить город по умолчанию"},
    {"command": "settime", "description": "Настроить ежедневную рассылку"},
    {"command": "stopnotify", "description": "Отключить ежедневную рассылку"},
    {"command": "city", "description": "Показать сохраненный город"},
    {"command": "help", "description": "Справка по боту"},
]

HELP_TEXT = "\n".join(
    [
        "Привет! Я показываю текущую погоду ☀️",
        "",
        "Выберите действие на клавиатуре ниже или напишите город, например: Москва",
        "",
        "Кнопки:",
        f"{BUTTON_WEATHER} - погода для города по умолчанию",
        f"{BUTTON_TODAY} - сводка на день",
        f"{BUTTON_SET_CITY} - сохранить город по умолчанию",
        f"{BUTTON_MY_CITY} - показать сохраненный город",
        f"{BUTTON_SET_TIME} - ежедневная погода в выбранное время",
        f"{BUTTON_DISABLE_NOTIFICATIONS} - отключить ежедневную погоду",
        f"{BUTTON_HELP} - справка",
        "",
        "Числа и почтовые индексы я не принимаю: нужен именно город.",
    ]
)


class BotError(Exception):
    """A user-facing Telegram bot error."""


def get_database_path() -> str:
    return os.environ.get(BOT_DB_ENV, DEFAULT_DB_PATH).strip() or DEFAULT_DB_PATH


def init_database(db_path: str) -> None:
    directory = os.path.dirname(db_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                chat_id INTEGER PRIMARY KEY,
                default_city TEXT NOT NULL,
                location_name TEXT NOT NULL,
                region TEXT,
                country TEXT,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                notification_enabled INTEGER NOT NULL DEFAULT 0,
                notification_time TEXT,
                timezone TEXT,
                last_notification_date TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        ensure_column(connection, "user_settings", "last_notification_date", "TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_state (
                chat_id INTEGER PRIMARY KEY,
                pending_action TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_users (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS banned_users (
                chat_id INTEGER PRIMARY KEY,
                reason TEXT,
                banned_by_username TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.commit()
    finally:
        connection.close()


def ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    columns = {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
        )


def place_to_location(place: dict[str, Any]) -> str:
    return ", ".join(
        part
        for part in [place.get("name"), place.get("admin1"), place.get("country")]
        if part
    )


def normalize_username(username: str | None) -> str:
    return (username or "").strip().lstrip("@").lower()


def get_admin_usernames() -> set[str]:
    raw_usernames = os.environ.get(ADMIN_USERNAMES_ENV, DEFAULT_ADMIN_USERNAMES)
    return {
        normalized
        for username in raw_usernames.split(",")
        if (normalized := normalize_username(username))
    }


def is_admin(username: str | None, admin_usernames: set[str] | None = None) -> bool:
    normalized = normalize_username(username)
    if not normalized:
        return False
    return normalized in (admin_usernames or get_admin_usernames())


def record_user_from_message(message: dict[str, Any], db_path: str) -> None:
    chat = message.get("chat")
    user = message.get("from")
    if not isinstance(chat, dict) or not isinstance(user, dict):
        return

    chat_id = chat.get("id")
    if not isinstance(chat_id, int):
        return

    init_database(db_path)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO bot_users (
                chat_id,
                username,
                first_name,
                last_name,
                last_seen_at
            )
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                last_seen_at = CURRENT_TIMESTAMP
            """,
            (
                chat_id,
                user.get("username"),
                user.get("first_name"),
                user.get("last_name"),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def get_admin_metrics(db_path: str) -> dict[str, int]:
    init_database(db_path)
    connection = sqlite3.connect(db_path)
    try:
        total_users = connection.execute("SELECT COUNT(*) FROM bot_users").fetchone()[0]
        users_with_city = connection.execute(
            "SELECT COUNT(*) FROM user_settings"
        ).fetchone()[0]
        enabled_notifications = connection.execute(
            """
            SELECT COUNT(*)
            FROM user_settings
            WHERE notification_enabled = 1
                AND notification_time IS NOT NULL
            """
        ).fetchone()[0]
        pending_actions = connection.execute(
            """
            SELECT COUNT(*)
            FROM user_state
            WHERE pending_action IS NOT NULL
            """
        ).fetchone()[0]
        banned_users = connection.execute(
            "SELECT COUNT(*) FROM banned_users"
        ).fetchone()[0]
    finally:
        connection.close()

    return {
        "total_users": total_users,
        "users_with_city": users_with_city,
        "enabled_notifications": enabled_notifications,
        "pending_actions": pending_actions,
        "banned_users": banned_users,
    }


def get_admin_users(db_path: str, limit: int = 20) -> list[dict[str, Any]]:
    init_database(db_path)
    connection = sqlite3.connect(db_path)
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                u.chat_id,
                u.username,
                u.first_name,
                u.last_name,
                u.created_at,
                u.last_seen_at,
                s.location_name,
                s.region,
                s.country,
                s.notification_enabled,
                s.notification_time,
                b.created_at AS banned_at,
                b.reason AS ban_reason
            FROM bot_users AS u
            LEFT JOIN user_settings AS s ON s.chat_id = u.chat_id
            LEFT JOIN banned_users AS b ON b.chat_id = u.chat_id
            ORDER BY u.last_seen_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        connection.close()

    return [dict(row) for row in rows]


def ban_user(
    chat_id: int,
    reason: str | None,
    banned_by_username: str | None,
    db_path: str,
) -> None:
    init_database(db_path)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO banned_users (
                chat_id,
                reason,
                banned_by_username,
                created_at
            )
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                reason = excluded.reason,
                banned_by_username = excluded.banned_by_username,
                created_at = CURRENT_TIMESTAMP
            """,
            (chat_id, reason, normalize_username(banned_by_username)),
        )
        connection.commit()
    finally:
        connection.close()


def unban_user(chat_id: int, db_path: str) -> bool:
    init_database(db_path)
    connection = sqlite3.connect(db_path)
    try:
        cursor = connection.execute(
            "DELETE FROM banned_users WHERE chat_id = ?",
            (chat_id,),
        )
        connection.commit()
        return cursor.rowcount > 0
    finally:
        connection.close()


def is_banned(chat_id: int, db_path: str) -> bool:
    init_database(db_path)
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT 1 FROM banned_users WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
    finally:
        connection.close()

    return row is not None


def get_banned_users(db_path: str, limit: int = 20) -> list[dict[str, Any]]:
    init_database(db_path)
    connection = sqlite3.connect(db_path)
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                b.chat_id,
                b.reason,
                b.banned_by_username,
                b.created_at,
                u.username,
                u.first_name,
                u.last_name
            FROM banned_users AS b
            LEFT JOIN bot_users AS u ON u.chat_id = b.chat_id
            ORDER BY b.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        connection.close()

    return [dict(row) for row in rows]


def save_default_city(chat_id: int, place: dict[str, Any], db_path: str) -> None:
    init_database(db_path)

    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO user_settings (
                chat_id,
                default_city,
                location_name,
                region,
                country,
                latitude,
                longitude,
                timezone,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                default_city = excluded.default_city,
                location_name = excluded.location_name,
                region = excluded.region,
                country = excluded.country,
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                timezone = COALESCE(excluded.timezone, user_settings.timezone),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                chat_id,
                place.get("name") or "Неизвестный город",
                place.get("name") or "Неизвестный город",
                place.get("admin1"),
                place.get("country"),
                place["latitude"],
                place["longitude"],
                place.get("timezone"),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def get_default_city(chat_id: int, db_path: str) -> dict[str, Any] | None:
    init_database(db_path)

    connection = sqlite3.connect(db_path)
    try:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT
                default_city,
                location_name,
                region,
                country,
                latitude,
                longitude,
                notification_enabled,
                notification_time,
                timezone,
                last_notification_date
            FROM user_settings
            WHERE chat_id = ?
            """,
            (chat_id,),
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        return None

    return {
        "name": row["location_name"],
        "admin1": row["region"],
        "country": row["country"],
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "default_city": row["default_city"],
        "notification_enabled": bool(row["notification_enabled"]),
        "notification_time": row["notification_time"],
        "timezone": row["timezone"],
        "last_notification_date": row["last_notification_date"],
    }


def parse_notification_time(value: str) -> str:
    raw_value = value.strip()
    try:
        parsed = datetime.strptime(raw_value, "%H:%M")
    except ValueError as error:
        raise ValueError("время нужно указать в формате ЧЧ:ММ, например 08:30") from error

    return parsed.strftime("%H:%M")


def set_notification_schedule(
    chat_id: int,
    notification_time: str,
    db_path: str,
) -> None:
    init_database(db_path)

    connection = sqlite3.connect(db_path)
    try:
        cursor = connection.execute(
            """
            UPDATE user_settings
            SET
                notification_enabled = 1,
                notification_time = ?,
                last_notification_date = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE chat_id = ?
            """,
            (notification_time, chat_id),
        )
        if cursor.rowcount == 0:
            raise BotError("сначала выберите город по умолчанию")
        connection.commit()
    finally:
        connection.close()


def disable_notifications(chat_id: int, db_path: str) -> bool:
    init_database(db_path)

    connection = sqlite3.connect(db_path)
    try:
        cursor = connection.execute(
            """
            UPDATE user_settings
            SET
                notification_enabled = 0,
                notification_time = NULL,
                last_notification_date = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE chat_id = ?
            """,
            (chat_id,),
        )
        connection.commit()
        return cursor.rowcount > 0
    finally:
        connection.close()


def refresh_default_city_timezone(chat_id: int, db_path: str) -> None:
    default_place = get_default_city(chat_id, db_path)
    if default_place is None or default_place.get("timezone"):
        return

    refreshed_place = find_city(default_place["default_city"])
    save_default_city(chat_id, refreshed_place, db_path)


def mark_notification_sent(chat_id: int, sent_date: str, db_path: str) -> None:
    init_database(db_path)

    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            UPDATE user_settings
            SET last_notification_date = ?, updated_at = CURRENT_TIMESTAMP
            WHERE chat_id = ?
            """,
            (sent_date, chat_id),
        )
        connection.commit()
    finally:
        connection.close()


def get_due_notifications(
    db_path: str,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    init_database(db_path)
    now = now or datetime.now(timezone.utc)
    due_notifications = []

    connection = sqlite3.connect(db_path)
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                user_settings.chat_id,
                user_settings.default_city,
                user_settings.location_name,
                user_settings.region,
                user_settings.country,
                user_settings.latitude,
                user_settings.longitude,
                user_settings.notification_time,
                user_settings.timezone,
                user_settings.last_notification_date
            FROM user_settings
            LEFT JOIN banned_users ON banned_users.chat_id = user_settings.chat_id
            WHERE user_settings.notification_enabled = 1
                AND user_settings.notification_time IS NOT NULL
                AND banned_users.chat_id IS NULL
            """
        ).fetchall()
    finally:
        connection.close()

    for row in rows:
        local_now = now_in_timezone(row["timezone"], now)
        today = local_now.date().isoformat()
        current_time = local_now.strftime("%H:%M")
        if row["last_notification_date"] == today:
            continue
        if current_time < row["notification_time"]:
            continue

        due_notifications.append(
            {
                "chat_id": row["chat_id"],
                "place": {
                    "name": row["location_name"],
                    "admin1": row["region"],
                    "country": row["country"],
                    "latitude": row["latitude"],
                    "longitude": row["longitude"],
                    "default_city": row["default_city"],
                    "timezone": row["timezone"],
                },
                "sent_date": today,
            }
        )

    return due_notifications


def now_in_timezone(timezone_name: str | None, now: datetime) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    try:
        target_timezone = ZoneInfo(timezone_name or "UTC")
    except ZoneInfoNotFoundError:
        target_timezone = timezone.utc

    return now.astimezone(target_timezone)


def process_due_notifications(token: str, db_path: str) -> None:
    for item in get_due_notifications(db_path):
        chat_id = item["chat_id"]
        sent_date = item["sent_date"]
        try:
            text = (
                "⏰ Ежедневная сводка\n\n"
                f"{get_today_summary_text_for_place(item['place'])}"
            )
            send_message(token, chat_id, text)
            mark_notification_sent(chat_id, sent_date, db_path)
        except BotError as error:
            print(f"Telegram error: {error}", file=sys.stderr, flush=True)
        except WeatherError as error:
            print(f"Daily weather error for chat {chat_id}: {error}", file=sys.stderr, flush=True)
            try:
                send_message(token, chat_id, build_daily_weather_error_text())
                mark_notification_sent(chat_id, sent_date, db_path)
            except BotError as bot_error:
                print(f"Telegram error: {bot_error}", file=sys.stderr, flush=True)


def set_pending_action(chat_id: int, pending_action: str | None, db_path: str) -> None:
    init_database(db_path)

    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO user_state (chat_id, pending_action, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                pending_action = excluded.pending_action,
                updated_at = CURRENT_TIMESTAMP
            """,
            (chat_id, pending_action),
        )
        connection.commit()
    finally:
        connection.close()


def get_pending_action(chat_id: int, db_path: str) -> str | None:
    init_database(db_path)

    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT pending_action FROM user_state WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        return None

    return row[0]


def get_weather_text_for_place(place: dict[str, Any]) -> str:
    weather = get_weather(place)
    return add_weather_emoji(format_weather(place, weather))


def get_weather_text_for_city(city: str) -> tuple[str, dict[str, Any]]:
    place = find_city(city)
    return get_weather_text_for_place(place), place


def build_daily_weather_error_text() -> str:
    return "\n".join(
        [
            "⚠️ Сегодня не удалось получить сводку погоды.",
            "Погодный сервис временно не ответил. Попробуйте /today позже, а завтра я снова пришлю рассылку.",
        ]
    )


def get_summary_greeting_for_place(
    place: dict[str, Any],
    now: datetime | None = None,
) -> str:
    local_now = now_in_timezone(place.get("timezone"), now or datetime.now(timezone.utc))
    return get_time_based_greeting(local_now)


def get_today_summary_text_for_place(
    place: dict[str, Any],
    now: datetime | None = None,
) -> str:
    weather = get_today_weather(place)
    return format_daily_summary(
        place,
        weather,
        greeting=get_summary_greeting_for_place(place, now),
    )


def get_today_summary_text_for_city(
    city: str,
    now: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    place = find_city(city)
    return get_today_summary_text_for_place(place, now), place


def add_weather_emoji(text: str) -> str:
    replacements = {
        "Погода:": "🌍 Погода:",
        "Время:": "🕒 Время:",
        "Сейчас:": "🌤️ Сейчас:",
        "Температура:": "🌡️ Температура:",
        "Ощущается как:": "🤔 Ощущается как:",
        "Влажность:": "💧 Влажность:",
        "Ветер:": "💨 Ветер:",
    }

    lines = []
    for line in text.splitlines():
        for old, new in replacements.items():
            if line.startswith(old):
                line = line.replace(old, new, 1)
                break
        lines.append(line)

    return "\n".join(lines)


def telegram_request(
    token: str,
    method: str,
    params: dict[str, Any] | None = None,
    timeout: int = 10,
) -> Any:
    url = f"{API_BASE_URL}/bot{token}/{method}"
    body = json.dumps(params or {}).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except HTTPError as error:
        raise BotError(read_telegram_error(error)) from error
    except URLError as error:
        raise BotError(f"не удалось подключиться к Telegram API: {error.reason}") from error
    except TimeoutError as error:
        raise BotError("Telegram API не ответил вовремя") from error

    if not payload.get("ok"):
        description = payload.get("description") or "неизвестная ошибка Telegram API"
        raise BotError(description)

    return payload.get("result")


def read_telegram_error(error: HTTPError) -> str:
    try:
        payload = json.loads(error.read().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return f"Telegram API вернул HTTP {error.code}"

    return payload.get("description") or f"Telegram API вернул HTTP {error.code}"


def delete_webhook(token: str, drop_pending_updates: bool = False) -> None:
    telegram_request(
        token,
        "deleteWebhook",
        {"drop_pending_updates": drop_pending_updates},
    )


def get_updates(token: str, offset: int | None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "timeout": LONG_POLL_TIMEOUT,
        "allowed_updates": ["message"],
    }
    if offset is not None:
        params["offset"] = offset

    result = telegram_request(
        token,
        "getUpdates",
        params,
        timeout=LONG_POLL_TIMEOUT + 10,
    )
    return result or []


def send_message(
    token: str,
    chat_id: int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> None:
    telegram_request(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
            "reply_markup": reply_markup or build_main_keyboard(),
        },
    )


def build_main_keyboard(include_admin: bool = False) -> dict[str, Any]:
    keyboard = [
        [{"text": BUTTON_WEATHER}, {"text": BUTTON_TODAY}],
        [{"text": BUTTON_SET_CITY}, {"text": BUTTON_MY_CITY}],
        [{"text": BUTTON_SET_TIME}, {"text": BUTTON_DISABLE_NOTIFICATIONS}],
        [{"text": BUTTON_HELP}],
    ]
    if include_admin:
        keyboard.append([{"text": BUTTON_ADMIN_MENU}])

    return {
        "keyboard": keyboard,
        "resize_keyboard": True,
        "is_persistent": True,
        "input_field_placeholder": "Выберите действие или напишите город",
    }


def build_admin_keyboard() -> dict[str, Any]:
    return {
        "keyboard": [
            [{"text": BUTTON_ADMIN_METRICS}, {"text": BUTTON_ADMIN_USERS}],
            [{"text": BUTTON_ADMIN_BAN}, {"text": BUTTON_ADMIN_UNBAN}],
            [{"text": BUTTON_ADMIN_BANS}],
            [{"text": BUTTON_MAIN_MENU}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "input_field_placeholder": "Выберите действие админки",
    }


def build_reply_markup_for_message(
    text: str,
    username: str | None,
    pending_action: str | None = None,
) -> dict[str, Any]:
    if not is_admin(username):
        return build_main_keyboard()

    message = text.strip()
    command = message.partition(" ")[0].split("@", 1)[0]
    if message == BUTTON_MAIN_MENU:
        return build_main_keyboard(include_admin=True)

    if (
        message in ADMIN_BUTTONS
        or command in ADMIN_COMMANDS
        or pending_action in {PENDING_ADMIN_BAN, PENDING_ADMIN_UNBAN}
    ):
        return build_admin_keyboard()

    return build_main_keyboard(include_admin=True)


def set_bot_commands(token: str) -> None:
    telegram_request(
        token,
        "setMyCommands",
        {
            "commands": BOT_COMMANDS,
            "scope": {"type": "default"},
            "language_code": "ru",
        },
    )


def build_response_text(
    text: str,
    chat_id: int,
    db_path: str | None = None,
    username: str | None = None,
) -> str:
    message = text.strip()
    db_path = db_path or get_database_path()

    if message in {"/start", "/help", BUTTON_HELP}:
        return HELP_TEXT

    command, _, argument = message.partition(" ")
    command = command.split("@", 1)[0]

    if message == BUTTON_MAIN_MENU:
        set_pending_action(chat_id, None, db_path)
        return "Основное меню открыто."

    if command in ADMIN_COMMANDS or message in ADMIN_BUTTONS:
        return build_admin_response(message, command, argument, chat_id, db_path, username)

    pending_action = get_pending_action(chat_id, db_path)
    if is_admin(username) and pending_action == PENDING_ADMIN_BAN:
        return build_ban_response(message, chat_id, db_path, username)

    if is_admin(username) and pending_action == PENDING_ADMIN_UNBAN:
        return build_unban_response(message, db_path)

    if message == BUTTON_SET_CITY:
        set_pending_action(chat_id, PENDING_SET_CITY, db_path)
        return "🏙️ Напишите название города, который нужно сохранить по умолчанию."

    if message == BUTTON_SET_TIME:
        if get_default_city(chat_id, db_path) is None:
            return f"🏙️ Сначала выберите город по умолчанию: нажмите «{BUTTON_SET_CITY}»."
        set_pending_action(chat_id, PENDING_SET_TIME, db_path)
        return "⏰ Напишите время ежедневной отправки в формате ЧЧ:ММ, например 08:30."

    if command == "/setcity" and not argument.strip():
        set_pending_action(chat_id, PENDING_SET_CITY, db_path)
        return "🏙️ Напишите название города, который нужно сохранить по умолчанию."

    if command == "/settime" and not argument.strip():
        if get_default_city(chat_id, db_path) is None:
            return f"🏙️ Сначала выберите город по умолчанию: нажмите «{BUTTON_SET_CITY}»."
        set_pending_action(chat_id, PENDING_SET_TIME, db_path)
        return "⏰ Напишите время ежедневной отправки в формате ЧЧ:ММ, например 08:30."

    if command == "/settime":
        return build_set_time_response(argument, chat_id, db_path)

    if command == "/stopnotify" or message == BUTTON_DISABLE_NOTIFICATIONS:
        if disable_notifications(chat_id, db_path):
            set_pending_action(chat_id, None, db_path)
            return "🔕 Ежедневная отправка погоды отключена."
        return f"🏙️ Город по умолчанию пока не выбран. Нажмите «{BUTTON_SET_CITY}»."

    if command == "/today" or message == BUTTON_TODAY:
        city = "" if message == BUTTON_TODAY else argument.strip()
        if city:
            try:
                summary_text, _ = get_today_summary_text_for_city(city)
                return summary_text
            except WeatherError as error:
                return f"Ошибка: {error}"

        default_place = get_default_city(chat_id, db_path)
        if default_place is None:
            return f"🏙️ Сначала выберите город по умолчанию: нажмите «{BUTTON_SET_CITY}»."

        try:
            return get_today_summary_text_for_place(default_place)
        except WeatherError as error:
            return f"Ошибка: {error}"

    if command == "/city" or message == BUTTON_MY_CITY:
        default_place = get_default_city(chat_id, db_path)
        if default_place is None:
            return f"🏙️ Город по умолчанию пока не выбран. Нажмите «{BUTTON_SET_CITY}»."

        notification_text = get_notification_status_text(default_place)
        return (
            f"🏙️ Ваш город по умолчанию: {place_to_location(default_place)}\n"
            f"{notification_text}"
        )

    if command == "/setcity":
        city = argument.strip()
        if not city:
            return "🏙️ Укажите город: /setcity Москва"

        try:
            place = find_city(city)
            save_default_city(chat_id, place, db_path)
            set_pending_action(chat_id, None, db_path)
            return (
                f"✅ Город по умолчанию сохранен: {place_to_location(place)}\n"
                f"Теперь кнопка «{BUTTON_WEATHER}» покажет погоду для него."
            )
        except WeatherError as error:
            return f"Ошибка: {error}"

    if command == "/weather" or message == BUTTON_WEATHER:
        city = "" if message == BUTTON_WEATHER else argument.strip()
        if city:
            try:
                weather_text, _ = get_weather_text_for_city(city)
                return weather_text
            except WeatherError as error:
                return f"Ошибка: {error}"

        default_place = get_default_city(chat_id, db_path)
        if default_place is None:
            return f"🏙️ Сначала выберите город по умолчанию: нажмите «{BUTTON_SET_CITY}»."

        try:
            return get_weather_text_for_place(default_place)
        except WeatherError as error:
            return f"Ошибка: {error}"

    if message.startswith("/"):
        return "Не знаю такую команду. Напишите /help или отправьте название города."

    if not message:
        return "Напишите название города."

    if pending_action == PENDING_SET_CITY:
        try:
            place = find_city(message)
            save_default_city(chat_id, place, db_path)
            set_pending_action(chat_id, None, db_path)
            return (
                f"✅ Город по умолчанию сохранен: {place_to_location(place)}\n"
                f"Теперь кнопка «{BUTTON_WEATHER}» покажет погоду для него."
            )
        except WeatherError as error:
            return f"Ошибка: {error}\nНапишите другой город."

    if pending_action == PENDING_SET_TIME:
        return build_set_time_response(message, chat_id, db_path)

    try:
        weather_text, _ = get_weather_text_for_city(message)
        return (
            f"{weather_text}\n\n"
            f"💾 Чтобы сохранить этот город по умолчанию, нажмите «{BUTTON_SET_CITY}»."
        )
    except WeatherError as error:
        return f"Ошибка: {error}"


def build_admin_response(
    message: str,
    command: str,
    argument: str,
    chat_id: int,
    db_path: str,
    username: str | None,
) -> str:
    if not is_admin(username):
        return "⛔ Команда доступна только администратору."

    if command == "/admin" or message == BUTTON_ADMIN_MENU:
        set_pending_action(chat_id, None, db_path)
        return "\n".join(
            [
                "🛠 Админка weather-бота",
                "",
                f"{BUTTON_ADMIN_METRICS} - базовые метрики",
                f"{BUTTON_ADMIN_USERS} - последние пользователи",
                f"{BUTTON_ADMIN_BAN} - забанить пользователя по chat_id",
                f"{BUTTON_ADMIN_UNBAN} - снять бан по chat_id",
                f"{BUTTON_ADMIN_BANS} - список банов",
                f"{BUTTON_MAIN_MENU} - вернуться к погоде",
                "",
                "Также работают команды: /admin_metrics, /admin_users, /ban, /unban, /bans",
            ]
        )

    if command == "/admin_metrics" or message == BUTTON_ADMIN_METRICS:
        return format_admin_metrics(get_admin_metrics(db_path))

    if command == "/admin_users" or message == BUTTON_ADMIN_USERS:
        return format_admin_users(get_admin_users(db_path))

    if command in {"/admin_bans", "/bans"} or message == BUTTON_ADMIN_BANS:
        return format_banned_users(get_banned_users(db_path))

    if command in {"/admin_ban", "/ban"} or message == BUTTON_ADMIN_BAN:
        if message != BUTTON_ADMIN_BAN and argument.strip():
            return build_ban_response(argument, chat_id, db_path, username)
        set_pending_action(chat_id, PENDING_ADMIN_BAN, db_path)
        return "⛔ Напишите chat_id для бана и причину, например: 12345 спам"

    if command in {"/admin_unban", "/unban"} or message == BUTTON_ADMIN_UNBAN:
        if message != BUTTON_ADMIN_UNBAN and argument.strip():
            return build_unban_response(argument, db_path)
        set_pending_action(chat_id, PENDING_ADMIN_UNBAN, db_path)
        return "✅ Напишите chat_id пользователя, которого нужно разбанить."

    return "Не знаю такую админ-команду. Используйте /admin."


def parse_chat_id_argument(value: str) -> tuple[int, str]:
    chat_id_text, _, reason = value.strip().partition(" ")
    if not chat_id_text:
        raise BotError("укажите chat_id пользователя")

    try:
        chat_id = int(chat_id_text)
    except ValueError as error:
        raise BotError("chat_id должен быть числом") from error

    return chat_id, reason.strip()


def build_ban_response(
    value: str,
    admin_chat_id: int,
    db_path: str,
    username: str | None,
) -> str:
    try:
        target_chat_id, reason = parse_chat_id_argument(value)
    except BotError as error:
        return f"Ошибка: {error}\nНапишите chat_id еще раз."

    if target_chat_id == admin_chat_id:
        return "⛔ Нельзя забанить самого себя."

    ban_user(target_chat_id, reason or None, username, db_path)
    set_pending_action(admin_chat_id, None, db_path)
    reason_text = f"\nПричина: {reason}" if reason else ""
    return f"✅ Пользователь {target_chat_id} забанен.{reason_text}"


def build_unban_response(value: str, db_path: str) -> str:
    try:
        target_chat_id, _ = parse_chat_id_argument(value)
    except BotError as error:
        return f"Ошибка: {error}\nНапишите chat_id еще раз."

    if unban_user(target_chat_id, db_path):
        return f"✅ Пользователь {target_chat_id} разбанен."
    return f"Пользователь {target_chat_id} не был в бане."


def format_admin_metrics(metrics: dict[str, int]) -> str:
    return "\n".join(
        [
            "📊 Метрики",
            f"👥 Пользователей: {metrics['total_users']}",
            f"🏙 С городом по умолчанию: {metrics['users_with_city']}",
            f"⏰ Активных рассылок: {metrics['enabled_notifications']}",
            f"🚫 В бане: {metrics['banned_users']}",
            f"✍️ Незавершенных диалогов: {metrics['pending_actions']}",
        ]
    )


def format_admin_users(users: list[dict[str, Any]]) -> str:
    if not users:
        return "👥 Пользователей пока нет."

    lines = ["👥 Последние пользователи:"]
    for index, user in enumerate(users, start=1):
        username = user.get("username")
        display_name = " ".join(
            part
            for part in [user.get("first_name"), user.get("last_name")]
            if part
        )
        identity = f"@{username}" if username else (display_name or "без username")
        city = place_to_location(
            {
                "name": user.get("location_name"),
                "admin1": user.get("region"),
                "country": user.get("country"),
            }
        )
        city_text = city or "город не выбран"
        notification_text = "рассылка выкл"
        if user.get("notification_enabled") and user.get("notification_time"):
            notification_text = f"рассылка {user['notification_time']}"
        ban_text = "забанен" if user.get("banned_at") else "активен"
        lines.append(
            f"{index}. {identity} | chat_id {user['chat_id']} | "
            f"{city_text} | {notification_text} | {ban_text}"
        )

    return "\n".join(lines)


def format_banned_users(users: list[dict[str, Any]]) -> str:
    if not users:
        return "🚫 Бан-лист пуст."

    lines = ["🚫 Бан-лист:"]
    for index, user in enumerate(users, start=1):
        username = user.get("username")
        display_name = " ".join(
            part
            for part in [user.get("first_name"), user.get("last_name")]
            if part
        )
        identity = f"@{username}" if username else (display_name or "без username")
        reason = user.get("reason") or "без причины"
        lines.append(
            f"{index}. {identity} | chat_id {user['chat_id']} | {reason}"
        )

    return "\n".join(lines)


def build_set_time_response(value: str, chat_id: int, db_path: str) -> str:
    if get_default_city(chat_id, db_path) is None:
        return f"🏙️ Сначала выберите город по умолчанию: нажмите «{BUTTON_SET_CITY}»."

    try:
        notification_time = parse_notification_time(value)
        try:
            refresh_default_city_timezone(chat_id, db_path)
        except WeatherError:
            pass
        set_notification_schedule(chat_id, notification_time, db_path)
        set_pending_action(chat_id, None, db_path)
    except ValueError as error:
        return f"Ошибка: {error}\nНапишите время еще раз."
    except BotError as error:
        return f"Ошибка: {error}"

    default_place = get_default_city(chat_id, db_path)
    timezone_name = default_place.get("timezone") or "UTC"
    return (
        f"✅ Ежедневная погода включена на {notification_time}.\n"
        f"Город: {place_to_location(default_place)}\n"
        f"Часовой пояс: {timezone_name}"
    )


def get_notification_status_text(default_place: dict[str, Any]) -> str:
    if not default_place.get("notification_enabled"):
        return "⏰ Ежедневная отправка выключена."

    timezone_name = default_place.get("timezone") or "UTC"
    return (
        "⏰ Ежедневная отправка включена: "
        f"{default_place.get('notification_time')} ({timezone_name})."
    )


def handle_update(token: str, update: dict[str, Any], db_path: str | None = None) -> None:
    message = update.get("message")
    if not isinstance(message, dict):
        return

    text = message.get("text")
    chat = message.get("chat")
    if not isinstance(text, str) or not isinstance(chat, dict):
        return

    chat_id = chat.get("id")
    if not isinstance(chat_id, int):
        return

    resolved_db_path = db_path or get_database_path()
    record_user_from_message(message, resolved_db_path)

    user = message.get("from")
    username = user.get("username") if isinstance(user, dict) else None
    if is_banned(chat_id, resolved_db_path) and not is_admin(username):
        send_message(
            token,
            chat_id,
            "⛔ Вы заблокированы и больше не можете пользоваться ботом.",
            reply_markup=build_main_keyboard(),
        )
        return

    pending_action = get_pending_action(chat_id, resolved_db_path)
    reply_markup = build_reply_markup_for_message(text, username, pending_action)
    send_message(
        token,
        chat_id,
        build_response_text(text, chat_id, resolved_db_path, username=username),
        reply_markup=reply_markup,
    )


def run_polling(token: str, db_path: str) -> None:
    offset = None
    init_database(db_path)
    delete_webhook(token, drop_pending_updates=False)
    set_bot_commands(token)
    print("Weather bot started", flush=True)

    while True:
        try:
            updates = get_updates(token, offset)
        except BotError as error:
            print(f"Telegram error: {error}", file=sys.stderr, flush=True)
            time.sleep(5)
            continue

        process_due_notifications(token, db_path)

        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                offset = update_id + 1

            try:
                handle_update(token, update, db_path)
            except BotError as error:
                print(f"Telegram error: {error}", file=sys.stderr, flush=True)
            except Exception as error:
                print(f"Unexpected error: {error}", file=sys.stderr, flush=True)

        process_due_notifications(token, db_path)


def main() -> int:
    token = os.environ.get(BOT_TOKEN_ENV, "").strip()
    if not token:
        print(f"Ошибка: задайте переменную окружения {BOT_TOKEN_ENV}", file=sys.stderr)
        return 1

    run_polling(token, get_database_path())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
