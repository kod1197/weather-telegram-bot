import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import bot


class FakeResponse(io.StringIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class BotTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "bot.sqlite3")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_build_response_text_returns_help_for_start(self):
        self.assertIn(
            "текущую погоду",
            bot.build_response_text("/start", 123, self.db_path),
        )

    def test_build_response_text_returns_help_for_help_button(self):
        self.assertIn(
            "Кнопки:",
            bot.build_response_text(bot.BUTTON_HELP, 123, self.db_path),
        )

    def test_build_response_text_returns_unknown_command_message(self):
        response = bot.build_response_text("/unknown", 123, self.db_path)

        self.assertIn("Не знаю такую команду", response)

    def test_get_admin_usernames_uses_default_admin(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(bot.get_admin_usernames(), {"kod1197"})

    def test_get_admin_usernames_reads_comma_separated_env(self):
        with patch.dict(os.environ, {bot.ADMIN_USERNAMES_ENV: "@Kod1197, helper"}):
            self.assertEqual(bot.get_admin_usernames(), {"kod1197", "helper"})

    def test_is_admin_normalizes_username(self):
        self.assertTrue(bot.is_admin("@Kod1197", {"kod1197"}))
        self.assertFalse(bot.is_admin(None, {"kod1197"}))
        self.assertFalse(bot.is_admin("guest", {"kod1197"}))

    def test_admin_command_denies_non_admin(self):
        response = bot.build_response_text(
            "/admin",
            123,
            self.db_path,
            username="guest",
        )

        self.assertIn("только администратору", response)

    def test_admin_help_allows_configured_admin(self):
        response = bot.build_response_text(
            "/admin",
            123,
            self.db_path,
            username="kod1197",
        )

        self.assertIn("Админка", response)
        self.assertIn("/admin_metrics", response)
        self.assertIn("/admin_users", response)

    def test_admin_metrics_counts_users_settings_notifications_and_state(self):
        bot.record_user_from_message(
            {
                "chat": {"id": 123},
                "from": {"username": "kod1197", "first_name": "Admin"},
            },
            self.db_path,
        )
        bot.record_user_from_message(
            {
                "chat": {"id": 456},
                "from": {"username": "guest", "first_name": "Guest"},
            },
            self.db_path,
        )
        bot.save_default_city(
            123,
            {
                "name": "Москва",
                "admin1": "Москва",
                "country": "Россия",
                "latitude": 55.75,
                "longitude": 37.62,
            },
            self.db_path,
        )
        bot.set_notification_schedule(123, "08:30", self.db_path)
        bot.set_pending_action(456, bot.PENDING_SET_CITY, self.db_path)
        bot.ban_user(456, "spam", "kod1197", self.db_path)

        response = bot.build_response_text(
            "/admin_metrics",
            123,
            self.db_path,
            username="kod1197",
        )

        self.assertIn("Пользователей: 2", response)
        self.assertIn("С городом по умолчанию: 1", response)
        self.assertIn("Активных рассылок: 1", response)
        self.assertIn("В бане: 1", response)
        self.assertIn("Незавершенных диалогов: 1", response)

    def test_admin_users_lists_recent_users_with_city_and_notification(self):
        bot.record_user_from_message(
            {
                "chat": {"id": 123},
                "from": {"username": "kod1197", "first_name": "Admin"},
            },
            self.db_path,
        )
        bot.save_default_city(
            123,
            {
                "name": "Москва",
                "admin1": "Москва",
                "country": "Россия",
                "latitude": 55.75,
                "longitude": 37.62,
            },
            self.db_path,
        )
        bot.set_notification_schedule(123, "08:30", self.db_path)

        response = bot.build_response_text(
            "/admin_users",
            123,
            self.db_path,
            username="kod1197",
        )

        self.assertIn("@kod1197", response)
        self.assertIn("chat_id 123", response)
        self.assertIn("Москва, Москва, Россия", response)
        self.assertIn("рассылка 08:30", response)
        self.assertIn("активен", response)

    def test_admin_users_marks_banned_user(self):
        bot.record_user_from_message(
            {
                "chat": {"id": 456},
                "from": {"username": "bad_user", "first_name": "Bad"},
            },
            self.db_path,
        )
        bot.ban_user(456, "spam", "kod1197", self.db_path)

        response = bot.build_response_text(
            "/admin_users",
            123,
            self.db_path,
            username="kod1197",
        )

        self.assertIn("@bad_user", response)
        self.assertIn("забанен", response)

    def test_admin_button_opens_admin_menu(self):
        response = bot.build_response_text(
            bot.BUTTON_ADMIN_MENU,
            123,
            self.db_path,
            username="kod1197",
        )

        self.assertIn("Админка", response)
        self.assertIn(bot.BUTTON_ADMIN_BAN, response)

    def test_admin_ban_button_sets_pending_action(self):
        response = bot.build_response_text(
            bot.BUTTON_ADMIN_BAN,
            123,
            self.db_path,
            username="kod1197",
        )

        self.assertIn("Напишите chat_id", response)
        self.assertEqual(
            bot.get_pending_action(123, self.db_path),
            bot.PENDING_ADMIN_BAN,
        )

    def test_pending_admin_ban_bans_user_with_reason(self):
        bot.set_pending_action(123, bot.PENDING_ADMIN_BAN, self.db_path)

        response = bot.build_response_text(
            "456 spam",
            123,
            self.db_path,
            username="kod1197",
        )

        self.assertIn("456", response)
        self.assertTrue(bot.is_banned(456, self.db_path))
        self.assertIsNone(bot.get_pending_action(123, self.db_path))
        banned_users = bot.get_banned_users(self.db_path)
        self.assertEqual(banned_users[0]["reason"], "spam")

    def test_admin_cannot_ban_self(self):
        response = bot.build_response_text(
            "/ban 123",
            123,
            self.db_path,
            username="kod1197",
        )

        self.assertIn("самого себя", response)
        self.assertFalse(bot.is_banned(123, self.db_path))

    def test_admin_unban_removes_ban(self):
        bot.ban_user(456, "spam", "kod1197", self.db_path)

        response = bot.build_response_text(
            "/unban 456",
            123,
            self.db_path,
            username="kod1197",
        )

        self.assertIn("разбанен", response)
        self.assertFalse(bot.is_banned(456, self.db_path))

    def test_admin_bans_lists_banned_users(self):
        bot.record_user_from_message(
            {
                "chat": {"id": 456},
                "from": {"username": "bad_user", "first_name": "Bad"},
            },
            self.db_path,
        )
        bot.ban_user(456, "spam", "kod1197", self.db_path)

        response = bot.build_response_text(
            bot.BUTTON_ADMIN_BANS,
            123,
            self.db_path,
            username="kod1197",
        )

        self.assertIn("Бан-лист", response)
        self.assertIn("@bad_user", response)
        self.assertIn("spam", response)

    def test_admin_commands_are_not_in_default_bot_commands(self):
        command_names = [command["command"] for command in bot.BOT_COMMANDS]

        self.assertNotIn("admin", command_names)
        self.assertNotIn("admin_metrics", command_names)
        self.assertNotIn("admin_users", command_names)

    def test_build_response_text_returns_weather_for_plain_city_with_emoji(self):
        place = {"name": "Москва", "admin1": "Москва", "country": "Россия"}
        forecast = {
            "current": {
                "time": "2026-07-02T15:45",
                "temperature_2m": 30.2,
                "apparent_temperature": 30.2,
                "relative_humidity_2m": 36,
                "weather_code": 2,
                "wind_speed_10m": 9.6,
            },
            "current_units": {},
        }

        with patch("bot.find_city", return_value=place):
            with patch("bot.get_weather", return_value=forecast):
                response = bot.build_response_text("Москва", 123, self.db_path)

        self.assertIn("🌍 Погода: Москва", response)
        self.assertIn("🌡️ Температура:", response)
        self.assertIn(bot.BUTTON_SET_CITY, response)

    def test_build_response_text_returns_validation_error(self):
        response = bot.build_response_text("123", 123, self.db_path)

        self.assertIn("Ошибка:", response)
        self.assertIn("число или индекс", response)

    def test_setcity_saves_default_city(self):
        place = {
            "name": "Москва",
            "admin1": "Москва",
            "country": "Россия",
            "latitude": 55.75,
            "longitude": 37.62,
        }

        with patch("bot.find_city", return_value=place):
            response = bot.build_response_text("/setcity Москва", 123, self.db_path)

        self.assertIn("Город по умолчанию сохранен", response)
        saved_place = bot.get_default_city(123, self.db_path)
        self.assertEqual(saved_place["name"], "Москва")
        self.assertEqual(saved_place["latitude"], 55.75)
        self.assertEqual(saved_place["notification_enabled"], False)
        self.assertIsNone(saved_place["notification_time"])

    def test_setcity_without_argument_asks_for_city_and_sets_pending_action(self):
        response = bot.build_response_text("/setcity", 123, self.db_path)

        self.assertIn("Напишите название города", response)
        self.assertEqual(
            bot.get_pending_action(123, self.db_path),
            bot.PENDING_SET_CITY,
        )

    def test_setcity_button_asks_for_city_and_sets_pending_action(self):
        response = bot.build_response_text(bot.BUTTON_SET_CITY, 123, self.db_path)

        self.assertIn("Напишите название города", response)
        self.assertEqual(
            bot.get_pending_action(123, self.db_path),
            bot.PENDING_SET_CITY,
        )

    def test_pending_setcity_saves_next_city_message(self):
        place = {
            "name": "Пермь",
            "admin1": "Пермский край",
            "country": "Россия",
            "latitude": 58.01,
            "longitude": 56.25,
        }
        bot.set_pending_action(123, bot.PENDING_SET_CITY, self.db_path)

        with patch("bot.find_city", return_value=place):
            response = bot.build_response_text("Пермь", 123, self.db_path)

        self.assertIn("Город по умолчанию сохранен", response)
        self.assertIn(bot.BUTTON_WEATHER, response)
        self.assertEqual(bot.get_default_city(123, self.db_path)["name"], "Пермь")
        self.assertIsNone(bot.get_pending_action(123, self.db_path))

    def test_pending_setcity_keeps_state_after_invalid_city(self):
        bot.set_pending_action(123, bot.PENDING_SET_CITY, self.db_path)

        response = bot.build_response_text("123", 123, self.db_path)

        self.assertIn("Ошибка:", response)
        self.assertEqual(
            bot.get_pending_action(123, self.db_path),
            bot.PENDING_SET_CITY,
        )

    def test_city_returns_saved_default_city(self):
        place = {
            "name": "Казань",
            "admin1": "Татарстан",
            "country": "Россия",
            "latitude": 55.79,
            "longitude": 49.12,
        }
        bot.save_default_city(123, place, self.db_path)

        response = bot.build_response_text("/city", 123, self.db_path)

        self.assertIn("Казань, Татарстан, Россия", response)

    def test_my_city_button_returns_saved_default_city(self):
        place = {
            "name": "Казань",
            "admin1": "Татарстан",
            "country": "Россия",
            "latitude": 55.79,
            "longitude": 49.12,
        }
        bot.save_default_city(123, place, self.db_path)

        response = bot.build_response_text(bot.BUTTON_MY_CITY, 123, self.db_path)

        self.assertIn("Казань, Татарстан, Россия", response)

    def test_city_returns_message_when_default_city_is_missing(self):
        response = bot.build_response_text("/city", 123, self.db_path)

        self.assertIn("пока не выбран", response)

    def test_weather_uses_saved_default_city(self):
        place = {
            "name": "Казань",
            "admin1": "Татарстан",
            "country": "Россия",
            "latitude": 55.79,
            "longitude": 49.12,
        }
        forecast = {
            "current": {
                "time": "2026-07-02T15:45",
                "temperature_2m": 25.0,
                "apparent_temperature": 25.0,
                "relative_humidity_2m": 50,
                "weather_code": 1,
                "wind_speed_10m": 4.0,
            },
            "current_units": {},
        }
        bot.save_default_city(123, place, self.db_path)

        with patch("bot.get_weather", return_value=forecast) as get_weather:
            response = bot.build_response_text("/weather", 123, self.db_path)

        self.assertIn("🌍 Погода: Казань", response)
        self.assertEqual(get_weather.call_args.args[0]["latitude"], 55.79)

    def test_weather_button_uses_saved_default_city(self):
        place = {
            "name": "Казань",
            "admin1": "Татарстан",
            "country": "Россия",
            "latitude": 55.79,
            "longitude": 49.12,
        }
        forecast = {
            "current": {
                "time": "2026-07-02T15:45",
                "temperature_2m": 25.0,
                "apparent_temperature": 25.0,
                "relative_humidity_2m": 50,
                "weather_code": 1,
                "wind_speed_10m": 4.0,
            },
            "current_units": {},
        }
        bot.save_default_city(123, place, self.db_path)

        with patch("bot.get_weather", return_value=forecast):
            response = bot.build_response_text(bot.BUTTON_WEATHER, 123, self.db_path)

        self.assertIn("🌍 Погода: Казань", response)

    def test_weather_without_default_city_asks_to_set_city(self):
        response = bot.build_response_text("/weather", 123, self.db_path)

        self.assertIn(bot.BUTTON_SET_CITY, response)

    def test_weather_with_argument_does_not_change_default_city(self):
        place = {"name": "Москва", "latitude": 55.75, "longitude": 37.62}
        forecast = {
            "current": {
                "time": "2026-07-02T15:45",
                "temperature_2m": 30.2,
                "apparent_temperature": 30.2,
                "relative_humidity_2m": 36,
                "weather_code": 2,
                "wind_speed_10m": 9.6,
            },
            "current_units": {},
        }

        with patch("bot.find_city", return_value=place):
            with patch("bot.get_weather", return_value=forecast):
                response = bot.build_response_text("/weather Москва", 123, self.db_path)

        self.assertIn("🌍 Погода: Москва", response)
        self.assertIsNone(bot.get_default_city(123, self.db_path))

    def test_send_message_includes_persistent_reply_keyboard(self):
        with patch("bot.telegram_request") as telegram_request:
            bot.send_message("token", 123, "hello")

        params = telegram_request.call_args.args[2]
        self.assertEqual(params["chat_id"], 123)
        self.assertEqual(params["reply_markup"]["keyboard"][0][0]["text"], bot.BUTTON_WEATHER)
        self.assertTrue(params["reply_markup"]["resize_keyboard"])
        self.assertTrue(params["reply_markup"]["is_persistent"])

    def test_main_keyboard_can_include_admin_menu_button(self):
        regular_labels = [
            button["text"]
            for row in bot.build_main_keyboard()["keyboard"]
            for button in row
        ]
        admin_labels = [
            button["text"]
            for row in bot.build_main_keyboard(include_admin=True)["keyboard"]
            for button in row
        ]

        self.assertNotIn(bot.BUTTON_ADMIN_MENU, regular_labels)
        self.assertIn(bot.BUTTON_ADMIN_MENU, admin_labels)

    def test_admin_keyboard_contains_admin_actions(self):
        labels = [
            button["text"]
            for row in bot.build_admin_keyboard()["keyboard"]
            for button in row
        ]

        self.assertIn(bot.BUTTON_ADMIN_METRICS, labels)
        self.assertIn(bot.BUTTON_ADMIN_USERS, labels)
        self.assertIn(bot.BUTTON_ADMIN_BAN, labels)
        self.assertIn(bot.BUTTON_ADMIN_UNBAN, labels)
        self.assertIn(bot.BUTTON_MAIN_MENU, labels)

    def test_reply_keyboard_contains_notification_buttons(self):
        keyboard = bot.build_main_keyboard()["keyboard"]

        labels = [button["text"] for row in keyboard for button in row]
        self.assertIn(bot.BUTTON_SET_TIME, labels)
        self.assertIn(bot.BUTTON_DISABLE_NOTIFICATIONS, labels)

    def test_parse_notification_time_accepts_short_and_padded_hours(self):
        self.assertEqual(bot.parse_notification_time("8:30"), "08:30")
        self.assertEqual(bot.parse_notification_time("08:30"), "08:30")

    def test_parse_notification_time_rejects_invalid_time(self):
        with self.assertRaisesRegex(ValueError, "формате"):
            bot.parse_notification_time("25:99")

    def test_settime_without_default_city_asks_to_set_city_first(self):
        response = bot.build_response_text(bot.BUTTON_SET_TIME, 123, self.db_path)

        self.assertIn(bot.BUTTON_SET_CITY, response)

    def test_settime_button_sets_pending_action_when_city_exists(self):
        place = {
            "name": "Москва",
            "latitude": 55.75,
            "longitude": 37.62,
            "timezone": "Europe/Moscow",
        }
        bot.save_default_city(123, place, self.db_path)

        response = bot.build_response_text(bot.BUTTON_SET_TIME, 123, self.db_path)

        self.assertIn("Напишите время", response)
        self.assertEqual(
            bot.get_pending_action(123, self.db_path),
            bot.PENDING_SET_TIME,
        )

    def test_settime_command_enables_daily_notifications(self):
        place = {
            "name": "Москва",
            "latitude": 55.75,
            "longitude": 37.62,
            "timezone": "Europe/Moscow",
        }
        bot.save_default_city(123, place, self.db_path)

        response = bot.build_response_text("/settime 08:30", 123, self.db_path)
        saved_place = bot.get_default_city(123, self.db_path)

        self.assertIn("Ежедневная погода включена", response)
        self.assertTrue(saved_place["notification_enabled"])
        self.assertEqual(saved_place["notification_time"], "08:30")
        self.assertEqual(saved_place["timezone"], "Europe/Moscow")

    def test_settime_refreshes_missing_timezone_when_possible(self):
        old_place = {"name": "Москва", "latitude": 55.75, "longitude": 37.62}
        refreshed_place = {
            "name": "Москва",
            "latitude": 55.75,
            "longitude": 37.62,
            "timezone": "Europe/Moscow",
        }
        bot.save_default_city(123, old_place, self.db_path)

        with patch("bot.find_city", return_value=refreshed_place):
            bot.build_response_text("/settime 08:30", 123, self.db_path)

        self.assertEqual(
            bot.get_default_city(123, self.db_path)["timezone"],
            "Europe/Moscow",
        )

    def test_pending_settime_saves_next_time_message(self):
        place = {
            "name": "Москва",
            "latitude": 55.75,
            "longitude": 37.62,
            "timezone": "Europe/Moscow",
        }
        bot.save_default_city(123, place, self.db_path)
        bot.set_pending_action(123, bot.PENDING_SET_TIME, self.db_path)

        response = bot.build_response_text("7:05", 123, self.db_path)
        saved_place = bot.get_default_city(123, self.db_path)

        self.assertIn("07:05", response)
        self.assertEqual(saved_place["notification_time"], "07:05")
        self.assertIsNone(bot.get_pending_action(123, self.db_path))

    def test_pending_settime_keeps_state_after_invalid_time(self):
        place = {"name": "Москва", "latitude": 55.75, "longitude": 37.62}
        bot.save_default_city(123, place, self.db_path)
        bot.set_pending_action(123, bot.PENDING_SET_TIME, self.db_path)

        response = bot.build_response_text("утром", 123, self.db_path)

        self.assertIn("Ошибка:", response)
        self.assertEqual(
            bot.get_pending_action(123, self.db_path),
            bot.PENDING_SET_TIME,
        )

    def test_disable_notifications_turns_schedule_off(self):
        place = {"name": "Москва", "latitude": 55.75, "longitude": 37.62}
        bot.save_default_city(123, place, self.db_path)
        bot.set_notification_schedule(123, "08:30", self.db_path)

        response = bot.build_response_text(bot.BUTTON_DISABLE_NOTIFICATIONS, 123, self.db_path)
        saved_place = bot.get_default_city(123, self.db_path)

        self.assertIn("отключена", response)
        self.assertFalse(saved_place["notification_enabled"])
        self.assertIsNone(saved_place["notification_time"])

    def test_city_shows_notification_status(self):
        place = {
            "name": "Москва",
            "latitude": 55.75,
            "longitude": 37.62,
            "timezone": "Europe/Moscow",
        }
        bot.save_default_city(123, place, self.db_path)
        bot.set_notification_schedule(123, "08:30", self.db_path)

        response = bot.build_response_text(bot.BUTTON_MY_CITY, 123, self.db_path)

        self.assertIn("08:30", response)
        self.assertIn("Europe/Moscow", response)

    def test_get_due_notifications_uses_city_timezone(self):
        place = {
            "name": "Москва",
            "latitude": 55.75,
            "longitude": 37.62,
            "timezone": "Europe/Moscow",
        }
        bot.save_default_city(123, place, self.db_path)
        bot.set_notification_schedule(123, "08:30", self.db_path)

        due = bot.get_due_notifications(
            self.db_path,
            datetime(2026, 7, 2, 5, 30, tzinfo=timezone.utc),
        )

        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["chat_id"], 123)
        self.assertEqual(due[0]["sent_date"], "2026-07-02")

    def test_get_due_notifications_skips_already_sent_today(self):
        place = {
            "name": "Москва",
            "latitude": 55.75,
            "longitude": 37.62,
            "timezone": "Europe/Moscow",
        }
        bot.save_default_city(123, place, self.db_path)
        bot.set_notification_schedule(123, "08:30", self.db_path)
        bot.mark_notification_sent(123, "2026-07-02", self.db_path)

        due = bot.get_due_notifications(
            self.db_path,
            datetime(2026, 7, 2, 6, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(due, [])

    def test_get_due_notifications_skips_banned_users(self):
        place = {
            "name": "Москва",
            "latitude": 55.75,
            "longitude": 37.62,
            "timezone": "Europe/Moscow",
        }
        bot.save_default_city(123, place, self.db_path)
        bot.set_notification_schedule(123, "08:30", self.db_path)
        bot.ban_user(123, "spam", "kod1197", self.db_path)

        due = bot.get_due_notifications(
            self.db_path,
            datetime(2026, 7, 2, 6, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(due, [])

    def test_process_due_notifications_sends_weather_and_marks_sent(self):
        place = {
            "name": "Москва",
            "latitude": 55.75,
            "longitude": 37.62,
            "timezone": "Europe/Moscow",
        }
        bot.save_default_city(123, place, self.db_path)
        bot.set_notification_schedule(123, "08:30", self.db_path)

        due_item = {
            "chat_id": 123,
            "place": place,
            "sent_date": "2026-07-02",
        }
        with patch("bot.get_due_notifications", return_value=[due_item]):
            with patch("bot.get_weather_text_for_place", return_value="weather"):
                with patch("bot.send_message") as send_message:
                    bot.process_due_notifications("token", self.db_path)

        send_message.assert_called_once()
        self.assertIn("Ежедневная погода", send_message.call_args.args[2])
        self.assertEqual(
            bot.get_default_city(123, self.db_path)["last_notification_date"],
            "2026-07-02",
        )

    def test_process_due_notifications_marks_sent_after_weather_error_message(self):
        place = {
            "name": "Москва",
            "latitude": 55.75,
            "longitude": 37.62,
            "timezone": "Europe/Moscow",
        }
        bot.save_default_city(123, place, self.db_path)
        bot.set_notification_schedule(123, "08:30", self.db_path)
        due_item = {
            "chat_id": 123,
            "place": place,
            "sent_date": "2026-07-02",
        }

        with patch("bot.get_due_notifications", return_value=[due_item]):
            with patch(
                "bot.get_weather_text_for_place",
                side_effect=bot.WeatherError("offline"),
            ):
                with patch("bot.send_message") as send_message:
                    bot.process_due_notifications("token", self.db_path)

        send_message.assert_called_once()
        self.assertIn("Ошибка ежедневной погоды", send_message.call_args.args[2])
        self.assertEqual(
            bot.get_default_city(123, self.db_path)["last_notification_date"],
            "2026-07-02",
        )

    def test_handle_update_sends_response_to_message_chat(self):
        update = {
            "message": {
                "chat": {"id": 12345},
                "text": "/help",
            }
        }

        with patch("bot.send_message") as send_message:
            bot.handle_update("token", update, self.db_path)

        send_message.assert_called_once()
        self.assertEqual(send_message.call_args.args[1], 12345)
        self.assertIn("текущую погоду", send_message.call_args.args[2])

    def test_handle_update_records_user_profile(self):
        update = {
            "message": {
                "chat": {"id": 12345},
                "from": {
                    "id": 777,
                    "username": "kod1197",
                    "first_name": "Konstantin",
                },
                "text": "/admin_users",
            }
        }

        with patch("bot.send_message") as send_message:
            bot.handle_update("token", update, self.db_path)

        users = bot.get_admin_users(self.db_path)
        self.assertEqual(users[0]["chat_id"], 12345)
        self.assertEqual(users[0]["username"], "kod1197")
        self.assertIn("@kod1197", send_message.call_args.args[2])

    def test_handle_update_sends_admin_keyboard_for_admin_menu_button(self):
        update = {
            "message": {
                "chat": {"id": 12345},
                "from": {"username": "kod1197"},
                "text": bot.BUTTON_ADMIN_MENU,
            }
        }

        with patch("bot.send_message") as send_message:
            bot.handle_update("token", update, self.db_path)

        reply_markup = send_message.call_args.kwargs["reply_markup"]
        labels = [button["text"] for row in reply_markup["keyboard"] for button in row]
        self.assertIn(bot.BUTTON_ADMIN_BAN, labels)
        self.assertIn(bot.BUTTON_MAIN_MENU, labels)

    def test_handle_update_sends_admin_button_to_admin_in_main_keyboard(self):
        update = {
            "message": {
                "chat": {"id": 12345},
                "from": {"username": "kod1197"},
                "text": "/help",
            }
        }

        with patch("bot.send_message") as send_message:
            bot.handle_update("token", update, self.db_path)

        reply_markup = send_message.call_args.kwargs["reply_markup"]
        labels = [button["text"] for row in reply_markup["keyboard"] for button in row]
        self.assertIn(bot.BUTTON_ADMIN_MENU, labels)

    def test_handle_update_blocks_banned_non_admin(self):
        bot.ban_user(12345, "spam", "kod1197", self.db_path)
        update = {
            "message": {
                "chat": {"id": 12345},
                "from": {"username": "guest"},
                "text": "/help",
            }
        }

        with patch("bot.send_message") as send_message:
            bot.handle_update("token", update, self.db_path)

        self.assertIn("заблокированы", send_message.call_args.args[2])

    def test_handle_update_does_not_block_admin_even_if_banned(self):
        bot.ban_user(12345, "mistake", "kod1197", self.db_path)
        update = {
            "message": {
                "chat": {"id": 12345},
                "from": {"username": "kod1197"},
                "text": "/admin_metrics",
            }
        }

        with patch("bot.send_message") as send_message:
            bot.handle_update("token", update, self.db_path)

        self.assertIn("Метрики", send_message.call_args.args[2])

    def test_record_user_from_message_updates_existing_user(self):
        bot.record_user_from_message(
            {
                "chat": {"id": 12345},
                "from": {"username": "old_name", "first_name": "Old"},
            },
            self.db_path,
        )
        bot.record_user_from_message(
            {
                "chat": {"id": 12345},
                "from": {"username": "new_name", "first_name": "New"},
            },
            self.db_path,
        )

        users = bot.get_admin_users(self.db_path)
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["username"], "new_name")
        self.assertEqual(users[0]["first_name"], "New")

    def test_handle_update_ignores_non_text_message(self):
        update = {
            "message": {
                "chat": {"id": 12345},
                "photo": [],
            }
        }

        with patch("bot.send_message") as send_message:
            bot.handle_update("token", update, self.db_path)

        send_message.assert_not_called()

    def test_telegram_request_returns_result(self):
        calls = []

        def fake_urlopen(request, timeout):
            calls.append((request, timeout))
            return FakeResponse('{"ok": true, "result": [{"update_id": 1}]}')

        with patch("bot.urlopen", fake_urlopen):
            result = bot.telegram_request("token", "getUpdates", {"timeout": 1})

        self.assertEqual(result, [{"update_id": 1}])
        self.assertEqual(calls[0][1], 10)
        self.assertIn("/bottoken/getUpdates", calls[0][0].full_url)

    def test_telegram_request_wraps_api_error(self):
        body = io.BytesIO(
            json.dumps({"ok": False, "description": "bad token"}).encode("utf-8")
        )
        error = HTTPError(
            url="https://api.telegram.org/bottoken/getMe",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=body,
        )

        with patch("bot.urlopen", side_effect=error):
            with self.assertRaisesRegex(bot.BotError, "bad token"):
                bot.telegram_request("token", "getMe")
        error.close()

    def test_telegram_request_wraps_network_error(self):
        with patch("bot.urlopen", side_effect=URLError("offline")):
            with self.assertRaisesRegex(bot.BotError, "offline"):
                bot.telegram_request("token", "getMe")

    def test_set_bot_commands_registers_default_menu(self):
        with patch("bot.telegram_request") as telegram_request:
            bot.set_bot_commands("token")

        telegram_request.assert_called_once()
        token, method, params = telegram_request.call_args.args
        self.assertEqual(token, "token")
        self.assertEqual(method, "setMyCommands")
        self.assertEqual(params["scope"], {"type": "default"})
        self.assertEqual(params["language_code"], "ru")
        self.assertEqual(
            [command["command"] for command in params["commands"]],
            ["weather", "setcity", "settime", "stopnotify", "city", "help"],
        )

    def test_run_polling_sets_menu_before_reading_updates(self):
        calls = []

        def fake_get_updates(token, offset):
            calls.append("get_updates")
            raise KeyboardInterrupt

        with patch("bot.init_database", side_effect=lambda db_path: calls.append("db")):
            with patch("bot.delete_webhook", side_effect=lambda *args, **kwargs: calls.append("webhook")):
                with patch("bot.set_bot_commands", side_effect=lambda token: calls.append("commands")):
                    with patch("bot.get_updates", side_effect=fake_get_updates):
                        with self.assertRaises(KeyboardInterrupt):
                            with redirect_stdout(io.StringIO()):
                                bot.run_polling("token", self.db_path)

        self.assertEqual(calls, ["db", "webhook", "commands", "get_updates"])


if __name__ == "__main__":
    unittest.main()
