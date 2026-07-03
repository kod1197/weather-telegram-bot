import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
        self.assertEqual(calls[0][1], 10)
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
