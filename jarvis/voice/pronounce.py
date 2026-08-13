"""Turning written text into something a synthesizer can say properly.

Kokoro handles ordinary prose well and specific things badly, and the failures
are not subtle -- it stutters, repeats syllables, or produces something that is
not the word at all. The reliable triggers:

  digits        "42%" and "13.3" get read digit-by-digit or skipped
  acronyms      "CPU" becomes a mangled attempt at a word
  units         "GB", "ms" are read as letters or dropped
  times         "1:17 PM" is unpredictable
  punctuation   doubled or stray marks cause audible stumbles
  long clauses  the model loses coherence and starts repeating

So the model's written output is converted to how it should be *spoken* before
synthesis. This runs after the markdown/URL stripping in tts.speakable.
"""
from __future__ import annotations

import re

ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven",
        "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
        "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
        "eighty", "ninety"]


def number_to_words(n: int) -> str:
    """Integer to spoken English. Handles what an assistant actually says."""
    if n < 0:
        return "minus " + number_to_words(-n)
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("-" + ONES[n % 10] if n % 10 else "")
    if n < 1000:
        rest = n % 100
        return (ONES[n // 100] + " hundred"
                + (" and " + number_to_words(rest) if rest else ""))
    for size, name in ((1_000_000_000, "billion"), (1_000_000, "million"),
                       (1000, "thousand")):
        if n >= size:
            rest = n % size
            return (number_to_words(n // size) + f" {name}"
                    + (" " + number_to_words(rest) if rest else ""))
    return str(n)


def _decimal_to_words(match: re.Match) -> str:
    whole, frac = match.group(1), match.group(2)
    spoken = number_to_words(int(whole)) + " point "
    return spoken + " ".join(ONES[int(d)] for d in frac)


def _int_to_words(match: re.Match) -> str:
    raw = match.group(0).replace(",", "")
    try:
        value = int(raw)
    except ValueError:
        return match.group(0)
    # Four-digit numbers in this range are almost always years. Deliberately
    # narrow: a wider window turned "1207 milliseconds" into "twelve seven".
    if 1900 <= value <= 2099 and len(raw) == 4:
        return f"{number_to_words(value // 100)} {number_to_words(value % 100)}" \
            if value % 100 else f"{number_to_words(value // 100)} hundred"
    return number_to_words(value)


# Read as letters, not attempted as a word. The spaces are what make Kokoro
# spell them rather than stumble.
ACRONYMS = {
    "CPU": "C P U", "GPU": "G P U", "RAM": "RAM", "SSD": "S S D",
    "HDD": "H D D", "USB": "U S B", "PDF": "P D F", "URL": "U R L",
    "HTTP": "H T T P", "HTTPS": "H T T P S", "API": "A P I",
    "AI": "A I", "PC": "P C", "OS": "O S", "IP": "I P", "ID": "I D",
    "UI": "U I", "TV": "T V", "AM": "A M", "PM": "P M", "GB": "gigabytes",
    "MB": "megabytes", "KB": "kilobytes", "TB": "terabytes",
    "GHz": "gigahertz", "MHz": "megahertz", "ms": "milliseconds",
    "FPS": "F P S", "RGB": "R G B", "VPN": "V P N", "SMS": "S M S",
}

# Words Kokoro reliably mangles, respelled phonetically. Kept small on purpose:
# each entry is a hack, and a long list means the wrong fix is being applied.
OVERRIDES = {
    "jarvis": "Jarvis",
    "wifi": "why-fye",
    "wi-fi": "why-fye",
    "async": "ay-sink",
    "cache": "cash",
    "gigabytes": "giga-bytes",
    "nicholas": "Nicholas",
    "spotify": "Spot-ify",
    "ryzen": "Rye-zen",
    "lenovo": "Le-no-vo",
    "gui": "gooey",
    "sql": "sequel",
    "linux": "linnux",
}

_UNITS = [
    (re.compile(r"\b(\d+(?:\.\d+)?)\s*%"), r"\1 percent"),
    (re.compile(r"\b(\d+(?:\.\d+)?)\s*GB\b", re.I), r"\1 gigabytes"),
    (re.compile(r"\b(\d+(?:\.\d+)?)\s*MB\b", re.I), r"\1 megabytes"),
    (re.compile(r"\b(\d+(?:\.\d+)?)\s*KB\b", re.I), r"\1 kilobytes"),
    (re.compile(r"\b(\d+(?:\.\d+)?)\s*TB\b", re.I), r"\1 terabytes"),
    (re.compile(r"\b(\d+(?:\.\d+)?)\s*ms\b"), r"\1 milliseconds"),
    (re.compile(r"\b(\d+(?:\.\d+)?)\s*°?\s*[FC]\b"), r"\1 degrees"),
    (re.compile(r"\b(\d+(?:\.\d+)?)\s*mph\b", re.I), r"\1 miles per hour"),
]

_TIME = re.compile(r"\b(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)?\b")
_DECIMAL = re.compile(r"\b(\d+)\.(\d+)\b")
_INTEGER = re.compile(r"\b\d[\d,]*\b")
_REPEATED_PUNCT = re.compile(r"([,.!?;:])\1+")
_STRAY = re.compile(r"\s+([,.!?;:])")
_MULTISPACE = re.compile(r"\s{2,}")


def _time_to_words(match: re.Match) -> str:
    hour, minute, meridiem = int(match.group(1)), int(match.group(2)), match.group(3)
    spoken = number_to_words(hour)
    if minute == 0:
        spoken += " o'clock" if not meridiem else ""
    elif minute < 10:
        spoken += " oh " + ONES[minute]
    else:
        spoken += " " + number_to_words(minute)
    if meridiem:
        spoken += " " + ("A M" if meridiem.lower() == "am" else "P M")
    return spoken


def for_speech(text: str) -> str:
    """Rewrite text as it should be spoken."""
    if not text:
        return ""

    # Order matters: units before bare numbers, times before decimals.
    for pattern, replacement in _UNITS:
        text = pattern.sub(replacement, text)
    text = _TIME.sub(_time_to_words, text)
    text = _DECIMAL.sub(_decimal_to_words, text)
    text = _INTEGER.sub(_int_to_words, text)

    # Acronyms, whole-word only, before the lowercase overrides.
    for acronym, spoken in ACRONYMS.items():
        text = re.sub(rf"\b{re.escape(acronym)}\b", spoken, text)

    def _override(match: re.Match) -> str:
        word = match.group(0)
        replacement = OVERRIDES.get(word.lower())
        return replacement if replacement else word

    text = re.sub(r"\b[\w-]+\b", _override, text)

    # Punctuation noise is a genuine cause of stutter.
    text = _REPEATED_PUNCT.sub(r"\1", text)
    text = _STRAY.sub(r"\1", text)
    return _MULTISPACE.sub(" ", text).strip()


def split_for_synthesis(text: str, max_chars: int = 180) -> list[str]:
    """Break text into chunks Kokoro can hold together.

    Past roughly two hundred characters the model starts losing coherence and
    repeating syllables. Splitting on clause boundaries keeps the prosody
    natural, and the speaker plays the pieces back to back with no gap.
    """
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []

    chunks: list[str] = []
    current = ""
    # Prefer sentence ends, fall back to clause boundaries.
    for part in re.split(r"(?<=[.!?])\s+|(?<=[,;:])\s+", text):
        if not part:
            continue
        if len(current) + len(part) + 1 <= max_chars:
            current = f"{current} {part}".strip()
        else:
            if current:
                chunks.append(current)
            # A single clause longer than the limit still has to be broken.
            while len(part) > max_chars:
                cut = part.rfind(" ", 0, max_chars) or max_chars
                chunks.append(part[:cut].strip())
                part = part[cut:].strip()
            current = part
    if current:
        chunks.append(current)
    return [c for c in chunks if c]
