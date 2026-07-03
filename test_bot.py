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
