"""Weather + geocoding tools backed by Open-Meteo (free, no API key)."""
import json

import httpx
from langchain_core.tools import tool

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "rime fog", 51: "light drizzle", 53: "drizzle", 55: "dense drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow", 73: "snow",
    75: "heavy snow", 80: "rain showers", 81: "heavy rain showers", 82: "violent rain showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "Laszlo-AgentRegistryDemo1/1.0 (Agent 365 demo)"


def _geocode_address(query: str) -> dict | None:
    # Street addresses: OpenStreetMap Nominatim (free, 1 req/sec fair-use policy).
    r = httpx.get(NOMINATIM_URL, params={"q": query, "format": "jsonv2", "addressdetails": 1, "limit": 1},
                  headers={"User-Agent": USER_AGENT}, timeout=15)
    r.raise_for_status()
    hits = r.json()
    if not hits:
        return None
    h, addr = hits[0], hits[0].get("address", {})
    return {
        "name": h.get("display_name"),
        "admin1": addr.get("state"),
        "country": addr.get("country"),
        "latitude": float(h["lat"]),
        "longitude": float(h["lon"]),
    }


def _geocode(query: str) -> dict | None:
    # Open-Meteo matches on place name; use the first comma-separated segment for "City, ST" input.
    name = query.split(",")[0].strip()
    looks_like_address = any(ch.isdigit() for ch in name)
    if not looks_like_address:
        r = httpx.get(GEOCODE_URL, params={"name": name, "count": 5, "language": "en", "format": "json"}, timeout=15)
        r.raise_for_status()
        results = r.json().get("results") or []
        if results:
            hint = query.lower()
            for res in results:
                if any(str(res.get(k, "")).lower() in hint for k in ("admin1", "country", "country_code") if res.get(k)):
                    return res
            return results[0]
    return _geocode_address(query)


@tool
def geocode(place: str) -> str:
    """Return latitude/longitude and location details for a city, place name or street address, e.g. 'San Francisco', 'Paris, France' or '1 Microsoft Way, Redmond, WA'."""
    res = _geocode(place)
    if not res:
        return json.dumps({"error": f"No location found for '{place}'"})
    return json.dumps({
        "name": res.get("name"),
        "region": res.get("admin1"),
        "country": res.get("country"),
        "latitude": res.get("latitude"),
        "longitude": res.get("longitude"),
        "timezone": res.get("timezone"),
        "population": res.get("population"),
    })


@tool
def get_weather(place: str) -> str:
    """Return the current weather (temperature °F/°C, wind, conditions) for a city or place name."""
    res = _geocode(place)
    if not res:
        return json.dumps({"error": f"No location found for '{place}'"})
    r = httpx.get(FORECAST_URL, params={
        "latitude": res["latitude"], "longitude": res["longitude"],
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "timezone": "auto",
    }, timeout=15)
    r.raise_for_status()
    cur = r.json().get("current", {})
    temp_f = cur.get("temperature_2m")
    return json.dumps({
        "location": f"{res.get('name')}, {res.get('admin1') or ''} {res.get('country') or ''}".strip(),
        "time": cur.get("time"),
        "temperature_f": temp_f,
        "temperature_c": round((temp_f - 32) * 5 / 9, 1) if temp_f is not None else None,
        "humidity_pct": cur.get("relative_humidity_2m"),
        "wind_mph": cur.get("wind_speed_10m"),
        "conditions": WEATHER_CODES.get(cur.get("weather_code"), "unknown"),
    })


ALL_TOOLS = [get_weather, geocode]
