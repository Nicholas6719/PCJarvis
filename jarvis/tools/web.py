"""Web access: search, page reading, weather, news.

All free and key-less. DuckDuckGo for search, Open-Meteo for weather. The one
rule throughout: results are summarized for speech, never dumped verbatim.
"""
from __future__ import annotations

import logging
import re

import httpx

from ..config import CONFIG
from .registry import tool

log = logging.getLogger("jarvis.tools.web")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " \
     "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"

# Extraction frequently "succeeds" on a consent wall, and a summary of a cookie
# notice presented as the article is worse than an outright failure.
_CONSENT = ("we and our partners", "accept all cookies", "cookie policy",
            "manage preferences", "privacy preferences", "enable javascript",
            "please enable cookies", "verify you are human",
            "checking your browser", "consent to the use of cookies")

_STOP = {"the", "a", "an", "of", "for", "and", "in", "on", "to", "is",
         "what", "how", "why", "latest", "news", "about", "any"}


def precise_location() -> tuple[float, float, str] | None:
    """Where he actually is, from the Windows location service.

    Measured on this machine: the Windows service is accurate to about 76
    metres, while the IP estimate landed in Cambridge -- 27.9 km away, which is
    a different city's weather entirely. IP geolocation reports where the
    *network* is, not where the laptop is.

    Returns (lat, lon, source) or None if location services are off.
    """
    try:
        import asyncio
        import concurrent.futures

        def read():
            from winsdk.windows.devices.geolocation import (
                Geolocator, PositionAccuracy)

            async def go():
                locator = Geolocator()
                locator.desired_accuracy = PositionAccuracy.HIGH
                position = await locator.get_geoposition_async()
                point = position.coordinate.point.position
                return (float(point.latitude), float(point.longitude),
                        float(position.coordinate.accuracy or 0))
            return asyncio.run(go())

        # WinRT is apartment-bound; give it its own thread every time.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            lat, lon, accuracy = pool.submit(read).result(timeout=12)
        log.info("using the Windows location service (accurate to ~%.0f m)",
                 accuracy)
        return lat, lon, "device"
    except Exception as e:
        log.info("Windows location unavailable (%s); falling back to the IP "
                 "estimate, which can be tens of kilometres out",
                 type(e).__name__)
        return None


def _looks_like_consent(text: str) -> bool:
    head = text[:900].lower()
    return sum(1 for phrase in _CONSENT if phrase in head) >= 2


def _relevance(query: str, results: list) -> float:
    """How much of the query actually appears in the results.

    A search engine always returns something. For a query it does not
    understand it returns confident, well-formatted, entirely unrelated pages,
    which then get relayed as findings. This measures whether the results have
    anything to do with what was asked.
    """
    terms = {w for w in re.findall(r"[a-z0-9]+", query.lower())
             if len(w) > 2 and w not in _STOP}
    if not terms:
        return 1.0
    blob = " ".join(f"{r.get('title', '')} {r.get('body', '')}"
                    for r in results).lower()
    return sum(1 for t in terms if t in blob) / len(terms)


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

        # Deliberately unnumbered and terse. A numbered list invites him to read
        # the list out, and a spoken numbered list is unbearable -- the answer
        # should be two sentences synthesised from these, not a recital.
        # Verified, not assumed. A search engine always returns something; if
        # what came back has nothing to do with the question, say so rather
        # than dressing it up as an answer.
        match = _relevance(query, results)
        if match < 0.34:
            return (f"I searched for '{query}' and nothing relevant came back "
                    f"-- the results are about other things entirely. Tell him "
                    f"you could not find anything on this. Do NOT answer from "
                    f"the titles below.\n"
                    + "\n".join(f"- {r.get('title', '')}" for r in results[:3]))

        header = (f"Search findings for '{query}' (summarise these in one or "
                  f"two spoken sentences; do NOT list them):")
        if match < 0.7:
            header += ("\n[only a partial match for the query -- say so if the "
                       "answer looks thin]")

        lines = [header]
        for r in results:
            body = (r.get("body") or "").strip().replace("\n", " ")
            lines.append(f"- {r.get('title', '')}: {body[:200]}")
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
            final_url = str(r.url)

        import trafilatura
        text = trafilatura.extract(html, include_comments=False,
                                   include_tables=False, favor_precision=True)
        if not text or not text.strip():
            return (f"I fetched {final_url} but could not extract any readable "
                    f"text from it. It may be a login wall, a consent page, or "
                    f"rendered entirely in JavaScript.")

        text = text.strip()
        # A redirect can silently land somewhere else entirely -- a login page,
        # a regional homepage. Say so rather than summarising the wrong thing.
        moved = ""
        if final_url.rstrip("/") != url.rstrip("/"):
            moved = f" (redirected from {url})"

        # Some pages "extract" successfully into a cookie banner. Summarising
        # that as the article is the failure this guard exists to prevent.
        if len(text) < 400:
            return (f"I fetched {final_url}{moved}, but there is very little "
                    f"readable text on it -- only {len(text)} characters, which "
                    f"is probably a banner or a stub rather than an article. "
                    f"Here is all of it:\n{text}")

        if _looks_like_consent(text):
            return (f"I fetched {final_url}{moved}, but what came back reads "
                    f"like a cookie or consent notice rather than the article "
                    f"itself:\n{text[:600]}")

        body = text[:3500]
        note = ("" if len(text) <= 3500
                else f"\n[showing the first 3500 of {len(text)} characters]")
        return f"Content of {final_url}{moved} ({len(text)} characters):\n{body}{note}"
    except httpx.HTTPStatusError as e:
        return (f"That page returned {e.response.status_code}, so I could not "
                f"read it.")
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
                # Device location first. The IP estimate is where the
                # network is, not where the laptop is -- measured 27.9 km
                # out on this machine, which is a different city.
                fix = precise_location()
                if fix:
                    lat, lon, _ = fix
                    # Name the place from the coordinates, so he can tell at
                    # a glance whether it picked the right town.
                    name = "your location"
                    try:
                        rev = client.get(
                            "https://nominatim.openstreetmap.org/reverse",
                            params={"lat": lat, "lon": lon, "format": "json",
                                    "zoom": 12},
                            headers={"User-Agent": UA}).json()
                        addr = rev.get("address", {})
                        name = (addr.get("city") or addr.get("town")
                                or addr.get("village") or addr.get("suburb")
                                or addr.get("county") or "your location")
                    except Exception:
                        log.debug("reverse geocode failed", exc_info=True)
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
