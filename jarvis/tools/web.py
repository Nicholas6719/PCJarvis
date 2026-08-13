"""Web access: search, page reading, weather, news.

All free and key-less. DuckDuckGo for search, Open-Meteo for weather. The one
rule throughout: results are summarized for speech, never dumped verbatim.
"""
from __future__ import annotations

import logging

import httpx

from ..config import CONFIG
from .registry import tool

log = logging.getLogger("jarvis.tools.web")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " \
     "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"


@tool(category="web", speak_while_running=True)
def web_search(query: str, count: int = 5) -> str:
    """Search the web and return the top results.

    Use for current events, facts you are unsure of, or anything after your
    training cutoff.

    Args:
        query: What to search for.
        count: How many results to return.
    """
    try:
        from ddgs import DDGS

        count = max(1, min(int(count), CONFIG.get("tools.web_result_count", 5)))
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=count))
        if not results:
            return f"No results for {query}."

        lines = [f"Search results for '{query}':"]
        for i, r in enumerate(results, 1):
            body = (r.get("body") or "").strip().replace("\n", " ")
            lines.append(f"{i}. {r.get('title', '')} -- {body[:280]}")
        return "\n".join(lines)
    except Exception as e:
        log.exception("search failed")
        return f"The search failed: {e}"


@tool(category="web", speak_while_running=True)
def read_webpage(url: str) -> str:
    """Fetch a web page and extract its readable article text.

    Args:
        url: The full URL to read.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        with httpx.Client(follow_redirects=True, timeout=20.0,
                          headers={"User-Agent": UA}) as client:
            r = client.get(url)
            r.raise_for_status()
            html = r.text

        import trafilatura
        text = trafilatura.extract(html, include_comments=False,
                                   include_tables=False, favor_precision=True)
        if not text:
            return "I couldn't extract readable text from that page."
        return f"Content of {url}:\n{text[:3500]}"
    except httpx.HTTPStatusError as e:
        return f"That page returned {e.response.status_code}."
    except Exception as e:
        return f"Could not read that page: {e}"


@tool(category="web", speak_while_running=True)
def get_weather(location: str = "") -> str:
    """Get the current weather and today's forecast.

    Args:
        location: City name. Leave empty to use the current location.
    """
    try:
        with httpx.Client(timeout=15.0, headers={"User-Agent": UA}) as client:
            if location.strip():
                geo = client.get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    params={"name": location, "count": 1, "language": "en"},
                ).json()
                if not geo.get("results"):
                    return f"I couldn't find a place called {location}."
                place = geo["results"][0]
                lat, lon = place["latitude"], place["longitude"]
                name = place["name"]
            else:
                loc = client.get("http://ip-api.com/json/").json()
                lat, lon = loc["lat"], loc["lon"]
                name = loc.get("city", "your location")

            w = client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat, "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,apparent_"
                               "temperature,precipitation,weather_code,wind_speed_10m",
                    "daily": "temperature_2m_max,temperature_2m_min,"
                             "precipitation_probability_max",
                    "temperature_unit": "fahrenheit",
                    "wind_speed_unit": "mph",
                    "timezone": "auto",
                    "forecast_days": 1,
                },
            ).json()

        cur, daily = w["current"], w["daily"]
        return (
            f"In {name}: {WEATHER_CODES.get(cur['weather_code'], 'unclear skies')}, "
            f"{cur['temperature_2m']:.0f} degrees, feels like "
            f"{cur['apparent_temperature']:.0f}. "
            f"Wind {cur['wind_speed_10m']:.0f} miles per hour. "
            f"Today: high of {daily['temperature_2m_max'][0]:.0f}, low of "
            f"{daily['temperature_2m_min'][0]:.0f}, "
            f"{daily['precipitation_probability_max'][0]:.0f} percent chance of rain."
        )
    except Exception as e:
        log.exception("weather failed")
        return f"Could not get the weather: {e}"


@tool(category="web", speak_while_running=True)
def get_news(topic: str = "top stories", count: int = 5) -> str:
    """Get recent news headlines.

    Args:
        topic: The subject to get news about.
        count: How many headlines to return.
    """
    try:
        from ddgs import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.news(topic, max_results=max(1, min(int(count), 8))))
        if not results:
            return f"No recent news on {topic}."

        lines = [f"Recent headlines on {topic}:"]
        for i, r in enumerate(results, 1):
            body = (r.get("body") or "").strip().replace("\n", " ")
            lines.append(
                f"{i}. {r.get('title', '')} ({r.get('source', '')}) -- {body[:200]}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Could not fetch the news: {e}"


WEATHER_CODES = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "freezing fog", 51: "light drizzle", 53: "drizzle",
    55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain", 71: "light snow",
    73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers",
    81: "showers", 82: "violent showers", 85: "snow showers",
    86: "heavy snow showers", 95: "thunderstorms",
    96: "thunderstorms with hail", 99: "severe thunderstorms with hail",
}
