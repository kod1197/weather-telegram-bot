#!/usr/bin/env python3
"""Show current weather for a city using the Open-Meteo API."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
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


def get_today_weather(place: dict[str, Any]) -> dict[str, Any]:
    return get_json(
        FORECAST_URL,
        {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": ",".join(
                [
                    "temperature_2m",
                    "apparent_temperature",
                    "weather_code",
                    "wind_speed_10m",
                ]
            ),
            "daily": ",".join(
                [
                    "weather_code",
                    "temperature_2m_min",
                    "temperature_2m_max",
                    "precipitation_probability_max",
                    "wind_speed_10m_max",
                ]
            ),
            "forecast_days": 1,
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


def get_time_based_greeting(moment: datetime) -> str:
    hour = moment.hour
    if 5 <= hour < 12:
        return "Доброе утро!"
    if 12 <= hour < 18:
        return "Добрый день!"
    if 18 <= hour < 23:
        return "Добрый вечер!"
    return "Доброй ночи!"


def format_daily_summary(
    place: dict[str, Any],
    weather: dict[str, Any],
    greeting: str = "Здравствуйте!",
) -> str:
    current = weather.get("current")
    daily = weather.get("daily")
    if not isinstance(current, dict) and not isinstance(daily, dict):
        raise WeatherError("API вернул неожиданный формат ответа")

    current = current if isinstance(current, dict) else {}
    daily = daily if isinstance(daily, dict) else {}
    current_units = weather.get("current_units", {})
    daily_units = weather.get("daily_units", {})

    city = place.get("name", "Неизвестном городе")
    current_code = current.get("weather_code")
    daily_code = get_daily_value(daily, "weather_code")
    description = WEATHER_CODES.get(
        current_code if current_code is not None else daily_code,
        "погода без описания",
    )

    lines = [
        greeting,
        "",
        f"Сегодня в {city}:",
    ]

    current_temp = current.get("temperature_2m")
    current_temp_text = format_temperature(
        current_temp,
        current_units.get("temperature_2m", "°C"),
    )
    if current_temp_text:
        lines.append(f"Сейчас: {current_temp_text}, {description}")
    else:
        lines.append(f"Сейчас: {description}")

    apparent_temp_text = format_temperature(
        current.get("apparent_temperature"),
        current_units.get("apparent_temperature", "°C"),
    )
    if apparent_temp_text:
        lines.append(f"Ощущается как: {apparent_temp_text}")

    min_temp = get_daily_value(daily, "temperature_2m_min")
    max_temp = get_daily_value(daily, "temperature_2m_max")
    min_temp_text = format_temperature(
        min_temp,
        daily_units.get("temperature_2m_min", "°C"),
    )
    max_temp_text = format_temperature(
        max_temp,
        daily_units.get("temperature_2m_max", "°C"),
    )
    if min_temp_text and max_temp_text:
        lines.append(f"Днем: от {min_temp_text} до {max_temp_text}")

    precipitation = get_daily_value(daily, "precipitation_probability_max")
    precipitation_text = format_plain_value(
        precipitation,
        daily_units.get("precipitation_probability_max", "%"),
    )
    if precipitation_text:
        lines.append(f"Осадки: вероятность до {precipitation_text}")

    wind = get_daily_value(daily, "wind_speed_10m_max")
    wind_text = format_plain_value(wind, daily_units.get("wind_speed_10m_max", "км/ч"))
    if wind_text:
        lines.append(f"Ветер: до {wind_text}")

    lines.extend(["", f"Совет: {build_daily_advice(min_temp, max_temp, precipitation, wind)}"])
    return "\n".join(lines)


def get_daily_value(daily: dict[str, Any], key: str) -> Any:
    value = daily.get(key)
    if isinstance(value, list):
        return value[0] if value else None
    return value


def format_temperature(value: Any, unit: str = "°C") -> str:
    if not isinstance(value, (int, float)):
        return ""

    sign = "+" if value > 0 else ""
    return f"{sign}{format_number(value)} {unit}".strip()


def format_plain_value(value: Any, unit: str = "") -> str:
    if not isinstance(value, (int, float)):
        return ""

    normalized_unit = "км/ч" if unit == "km/h" else unit
    return f"{format_number(value)} {normalized_unit}".strip()


def format_number(value: int | float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def build_daily_advice(
    min_temp: Any,
    max_temp: Any,
    precipitation_probability: Any,
    max_wind: Any,
) -> str:
    rainy = isinstance(precipitation_probability, (int, float)) and precipitation_probability >= 50
    cold = isinstance(max_temp, (int, float)) and max_temp <= 18
    very_cold = isinstance(max_temp, (int, float)) and max_temp <= 5
    hot = isinstance(max_temp, (int, float)) and max_temp >= 28
    windy = isinstance(max_wind, (int, float)) and max_wind >= 25

    if rainy and cold:
        return "лучше взять куртку и зонт."
    if rainy:
        return "зонт сегодня пригодится."
    if very_cold:
        return "оденьтесь теплее."
    if cold:
        return "лучше взять куртку."
    if hot:
        return "берите воду и избегайте перегрева."
    if windy:
        return "ветрено, выбирайте одежду поплотнее."
    if isinstance(min_temp, (int, float)) and min_temp <= 10:
        return "утром может быть прохладно, возьмите легкий слой."
    return "день выглядит спокойным, одевайтесь по погоде."


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
