import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import weather


class FakeResponse(io.StringIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class WeatherScriptTests(unittest.TestCase):
    def test_validate_city_name_accepts_text_city_names(self):
        for city in ["Москва", "New York", "Санкт-Петербург", "St. Petersburg"]:
            with self.subTest(city=city):
                weather.validate_city_name(city)

    def test_validate_city_name_rejects_numbers_and_digits(self):
        invalid_values = ["123", "Москва 123", "---"]

        for city in invalid_values:
            with self.subTest(city=city):
                with self.assertRaises(weather.WeatherError):
                    weather.validate_city_name(city)

    def test_get_json_returns_decoded_response_and_encodes_params(self):
        calls = []

        def fake_urlopen(url, timeout):
            calls.append((url, timeout))
            return FakeResponse('{"ok": true}')

        with patch("weather.urlopen", fake_urlopen):
            result = weather.get_json(
                "https://example.test/api",
                {"name": "New York", "count": 1},
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls[0][1], weather.API_TIMEOUT_SECONDS)
        self.assertIn("name=New+York", calls[0][0])
        self.assertIn("count=1", calls[0][0])

    def test_get_json_uses_api_error_reason_when_http_error_has_json_body(self):
        body = io.BytesIO(json.dumps({"reason": "bad request"}).encode("utf-8"))
        error = HTTPError(
            url="https://example.test/api",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=body,
        )

        with patch("weather.urlopen", side_effect=error):
            with self.assertRaisesRegex(weather.WeatherError, "bad request"):
                weather.get_json("https://example.test/api", {})
        error.close()

    def test_get_json_wraps_network_errors(self):
        with patch("weather.urlopen", side_effect=URLError("offline")):
            with self.assertRaisesRegex(weather.WeatherError, "offline"):
                weather.get_json("https://example.test/api", {})

    def test_get_json_retries_transient_network_errors(self):
        calls = []

        def fake_urlopen(url, timeout):
            calls.append((url, timeout))
            if len(calls) == 1:
                raise URLError("temporary timeout")
            return FakeResponse('{"ok": true}')

        with patch("weather.urlopen", fake_urlopen):
            result = weather.get_json("https://example.test/api", {})

        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(calls), 2)

    def test_find_city_returns_first_search_result(self):
        place = {"name": "Москва", "latitude": 55.75, "longitude": 37.62}

        with patch("weather.get_json", return_value={"results": [place]}) as get_json:
            result = weather.find_city("Москва")

        self.assertEqual(result, place)
        self.assertEqual(get_json.call_args.args[0], weather.GEOCODING_URL)
        self.assertEqual(get_json.call_args.args[1]["name"], "Москва")
        self.assertEqual(get_json.call_args.args[1]["count"], 1)

    def test_find_city_raises_when_city_is_missing(self):
        with patch("weather.get_json", return_value={}):
            with self.assertRaisesRegex(weather.WeatherError, "город не найден"):
                weather.find_city("НетТакогоГорода")

    def test_get_weather_requests_current_weather_for_coordinates(self):
        place = {"latitude": 55.75, "longitude": 37.62}

        with patch("weather.get_json", return_value={"current": {}}) as get_json:
            weather.get_weather(place)

        url, params = get_json.call_args.args
        self.assertEqual(url, weather.FORECAST_URL)
        self.assertEqual(params["latitude"], 55.75)
        self.assertEqual(params["longitude"], 37.62)
        self.assertEqual(params["timezone"], "auto")
        self.assertIn("temperature_2m", params["current"])
        self.assertIn("weather_code", params["current"])

    def test_get_today_weather_requests_current_and_daily_weather(self):
        place = {"latitude": 55.75, "longitude": 37.62}

        with patch("weather.get_json", return_value={"current": {}, "daily": {}}) as get_json:
            weather.get_today_weather(place)

        url, params = get_json.call_args.args
        self.assertEqual(url, weather.FORECAST_URL)
        self.assertEqual(params["latitude"], 55.75)
        self.assertEqual(params["longitude"], 37.62)
        self.assertEqual(params["timezone"], "auto")
        self.assertEqual(params["forecast_days"], 1)
        self.assertIn("temperature_2m", params["current"])
        self.assertIn("temperature_2m_min", params["daily"])
        self.assertIn("precipitation_probability_max", params["daily"])

    def test_format_weather_builds_readable_output(self):
        place = {"name": "Москва", "admin1": "Москва", "country": "Россия"}
        forecast = {
            "current": {
                "time": "2026-07-02T15:45",
                "temperature_2m": 30.2,
                "apparent_temperature": 31.0,
                "relative_humidity_2m": 36,
                "weather_code": 2,
                "wind_speed_10m": 9.6,
            },
            "current_units": {
                "temperature_2m": "°C",
                "apparent_temperature": "°C",
                "relative_humidity_2m": "%",
                "wind_speed_10m": "km/h",
            },
        }

        output = weather.format_weather(place, forecast)

        self.assertIn("Погода: Москва, Москва, Россия", output)
        self.assertIn("Сейчас: переменная облачность", output)
        self.assertIn("Температура: 30.2 °C", output)
        self.assertIn("Ветер: 9.6 km/h", output)

    def test_format_weather_rejects_unexpected_response(self):
        with self.assertRaisesRegex(weather.WeatherError, "неожиданный формат"):
            weather.format_weather({}, {"current": None})

    def test_format_daily_summary_builds_morning_summary_and_advice(self):
        place = {"name": "Москва", "admin1": "Москва", "country": "Россия"}
        forecast = {
            "current": {
                "temperature_2m": 12.0,
                "apparent_temperature": 10.0,
                "weather_code": 3,
            },
            "current_units": {
                "temperature_2m": "°C",
                "apparent_temperature": "°C",
            },
            "daily": {
                "temperature_2m_min": [8.0],
                "temperature_2m_max": [17.0],
                "precipitation_probability_max": [60],
                "wind_speed_10m_max": [25.0],
            },
            "daily_units": {
                "temperature_2m_min": "°C",
                "temperature_2m_max": "°C",
                "precipitation_probability_max": "%",
                "wind_speed_10m_max": "km/h",
            },
        }

        output = weather.format_daily_summary(place, forecast, greeting="Доброе утро!")

        self.assertIn("Доброе утро!", output)
        self.assertIn("📅 Сегодня в Москва:", output)
        self.assertIn("🌤️ Сейчас: +12 °C, пасмурно", output)
        self.assertIn("🤔 Ощущается как: +10 °C", output)
        self.assertIn("🌡️ Днем: от +8 °C до +17 °C", output)
        self.assertIn("☔ Осадки: вероятность до 60 %", output)
        self.assertIn("💨 Ветер: до 25 км/ч", output)
        self.assertIn("💡 Совет:", output)

    def test_format_daily_summary_skips_missing_optional_daily_fields(self):
        place = {"name": "Казань"}
        forecast = {
            "current": {
                "temperature_2m": -2,
                "weather_code": 71,
            },
            "current_units": {"temperature_2m": "°C"},
            "daily": {},
        }

        output = weather.format_daily_summary(place, forecast, greeting="Добрый день!")

        self.assertIn("📅 Сегодня в Казань:", output)
        self.assertIn("Добрый день!", output)
        self.assertIn("🌤️ Сейчас: -2 °C, слабый снег", output)
        self.assertNotIn("Осадки:", output)
        self.assertNotIn("Ветер:", output)

    def test_build_daily_advice_varies_phrases_for_similar_weather(self):
        first_advice = weather.build_daily_advice(8, 17, 60, 25)
        second_advice = weather.build_daily_advice(8, 17, 61, 25)

        self.assertNotEqual(first_advice, second_advice)
        self.assertIn("зонт", first_advice)
        self.assertIn("зонт", second_advice)

    def test_get_time_based_greeting_uses_request_hour(self):
        cases = [
            (datetime(2026, 7, 3, 7, 30), "Доброе утро!"),
            (datetime(2026, 7, 3, 13, 0), "Добрый день!"),
            (datetime(2026, 7, 3, 19, 15), "Добрый вечер!"),
            (datetime(2026, 7, 3, 2, 0), "Доброй ночи!"),
        ]

        for moment, expected in cases:
            with self.subTest(moment=moment):
                self.assertEqual(weather.get_time_based_greeting(moment), expected)

    def test_format_daily_summary_rejects_unexpected_response(self):
        with self.assertRaisesRegex(weather.WeatherError, "неожиданный формат"):
            weather.format_daily_summary({}, {"current": None, "daily": None})

    def test_main_prints_weather_for_city_argument(self):
        output = io.StringIO()
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

        with patch.object(sys, "argv", ["weather.py", "Москва"]):
            with patch("weather.find_city", return_value=place):
                with patch("weather.get_weather", return_value=forecast):
                    with redirect_stdout(output):
                        exit_code = weather.main()

        self.assertEqual(exit_code, 0)
        self.assertIn("Погода: Москва", output.getvalue())

    def test_main_returns_error_when_city_is_empty(self):
        errors = io.StringIO()

        with patch.object(sys, "argv", ["weather.py"]):
            with patch("builtins.input", return_value=""):
                with redirect_stderr(errors):
                    exit_code = weather.main()

        self.assertEqual(exit_code, 1)
        self.assertIn("город не указан", errors.getvalue())

    def test_main_rejects_numeric_city_before_api_lookup(self):
        errors = io.StringIO()

        with patch.object(sys, "argv", ["weather.py", "123"]):
            with patch("weather.find_city", wraps=weather.find_city) as find_city:
                with patch("weather.get_json") as get_json:
                    with redirect_stderr(errors):
                        exit_code = weather.main()

        self.assertEqual(exit_code, 1)
        self.assertIn("число или индекс", errors.getvalue())
        find_city.assert_called_once_with("123")
        get_json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
