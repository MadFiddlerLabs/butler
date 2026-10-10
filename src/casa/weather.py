"""One line of weather from Open-Meteo (free, no key), for settings.weather {place, latitude,
longitude}. Off when weather isn't configured."""

import json
import urllib.parse
import urllib.request

from .config import load_settings

WORDS = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "fog",
         51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain",
         65: "heavy rain", 80: "showers", 81: "showers", 82: "heavy showers", 95: "thunderstorms",
         96: "thunderstorms", 99: "thunderstorms"}


def today_line() -> str:
    settings = load_settings()
    w = settings.get("weather")
    if not w:
        return ""
    query = urllib.parse.urlencode({
        "latitude": w["latitude"], "longitude": w["longitude"],
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        "timezone": settings["timezone"], "forecast_days": 1,
        "temperature_unit": w.get("unit", "celsius")})
    with urllib.request.urlopen(f"https://api.open-meteo.com/v1/forecast?{query}", timeout=15) as r:
        d = json.load(r)["daily"]
    hi, lo = round(d["temperature_2m_max"][0]), round(d["temperature_2m_min"][0])
    rain = d["precipitation_probability_max"][0]
    sky = WORDS.get(d["weather_code"][0], "mixed")
    unit = "°F" if w.get("unit") == "fahrenheit" else "°C"
    out = f"{w['place']} today: {sky}, {lo}–{hi}{unit}"
    return out + (f", {rain}% chance of rain." if rain and rain >= 20 else ".")
