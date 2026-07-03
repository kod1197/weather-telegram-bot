#!/usr/bin/env python3
"""Show current weather for a city using the Open-Meteo API."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WEATHER_CODES = {
    0: "ясно",
    1: "преимущественно ясно",
    2: "переменная облачность",
    3: "пасмурно",
    45: "туман",
    48: "изморозь и туман",
    51: "слабая морось",
    53: "умеренная морось",
    55: "сильная морось",
    56: "слабая ледяная морось",
    57: "сильная ледяная морось",
    61: "слабый дождь",
    63: "умеренный дождь",
    65: "сильный дождь",
    66: "слабый ледяной дождь",
    67: "сильный ледяной дождь",
    71: "слабый снег",
    73: "умеренный снег",
    75: "сильный снег",
    77: "снежные зерна",
    80: "слабые ливни",
    81: "умеренные ливни",
    82: "сильные ливни",
    85: "слабый снегопад",
    86: "сильный снегопад",
    95: "гроза",
    96: "гроза с небольшим градом",
    99: "гроза с сильным градом",
}


class WeatherError(Exception):
    """A user-facing weather lookup error."""


def validate_city_name(city: str) -> None:
    if not any(char.isalpha() for char in city):
        raise WeatherError("нужно указать название города, а не число или индекс")

    if any(char.isdigit() for char in city):
        raise WeatherError("название города не должно содержать цифры")


def get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    full_url = f"{url}?{urlencode(params)}"

    try:
        with urlopen(full_url, timeout=10) as response:
            return json.load(response)
    except HTTPError as error:
        try:
            payload = json.loads(error.read().decode("utf-8"))
            reason = payload.get("reason")
        except (json.JSONDecodeError, UnicodeDecodeError):
            reason = None

        message = reason or f"HTTP {error.code}"
        raise WeatherError(f"API вернул ошибку: {message}") from error
    except URLError as error:
        raise WeatherError(f"не удалось подключиться к API: {error.reason}") from error
    except TimeoutError as error:
        raise WeatherError("API не ответил вовремя") from error


def find_city(city: str) -> dict[str, Any]:
    validate_city_name(city)

    data = get_json(
        GEOCODING_URL,
        {
            "name": city,
            "count": 1,
            "language": "ru",
            "format": "json",
        },
    )

    results = data.get("results") or []
    if not results:
        raise WeatherError(f"город не найден: {city}")

    return results[0]


def get_weather(place: dict[str, Any]) -> dict[str, Any]:
    return get_json(
        FORECAST_URL,
        {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": ",".join(
                [
                    "temperature_2m",
                    "apparent_temperature",
                    "relative_humidity_2m",
                    "weather_code",
                    "wind_speed_10m",
                ]
            ),
            "timezone": "auto",
        },
    )


def format_weather(place: dict[str, Any], weather: dict[str, Any]) -> str:
    current = weather.get("current")
    if not isinstance(current, dict):
        raise WeatherError("API вернул неожиданный формат ответа")

    units = weather.get("current_units", {})
    city = place.get("name", "Неизвестный город")
    country = place.get("country")
    region = place.get("admin1")
    location = ", ".join(part for part in [city, region, country] if part)

    code = current.get("weather_code")
    description = WEATHER_CODES.get(code, f"код погоды {code}")

    return "\n".join(
        [
            f"Погода: {location}",
            f"Время: {current.get('time', 'неизвестно')}",
            f"Сейчас: {description}",
            (
                "Температура: "
                f"{current.get('temperature_2m')} "
                f"{units.get('temperature_2m', '')}".rstrip()
            ),
            (
                "Ощущается как: "
                f"{current.get('apparent_temperature')} "
                f"{units.get('apparent_temperature', '')}".rstrip()
            ),
            (
                "Влажность: "
                f"{current.get('relative_humidity_2m')} "
                f"{units.get('relative_humidity_2m', '')}".rstrip()
            ),
            (
                "Ветер: "
                f"{current.get('wind_speed_10m')} "
                f"{units.get('wind_speed_10m', '')}".rstrip()
            ),
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Получить текущую погоду по названию города."
    )
    parser.add_argument(
        "city",
        nargs="*",
        help="название города, например: Москва или New York",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    city = " ".join(args.city).strip()

    if not city:
        city = input("Введите город: ").strip()

    if not city:
        print("Ошибка: город не указан", file=sys.stderr)
        return 1

    try:
        place = find_city(city)
        weather = get_weather(place)
        print(format_weather(place, weather))
    except WeatherError as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
