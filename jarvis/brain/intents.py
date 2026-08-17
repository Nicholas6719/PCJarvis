"""Deterministic commands: everything that should never wait on a model.

Two separate reasons for this layer, and both were learned the hard way.

**Speed.** A tool call through the LLM costs two round trips and five to fifteen
seconds. "Set a timer for ten seconds" answered in fifteen is not a timer, it is
an insult. Matched here, it is instant.

**Truth.** Measured on qwen2.5, the model acts on imperatives only 2 times in 10
-- it says "Pausing the music" and calls nothing. And even when it does call a
tool it paraphrases the result, which is how "Battery: 100%. Plugged in and
charging" reached the user as "fully charged, but not currently being charged".
Returning the tool's own words removes both failure modes.

The rule for adding an intent: it must be unambiguous in plain English, and a
wrong match must be harmless. Anything requiring judgement belongs to the model.
A `None` return means exactly that -- fall through, no harm done.
"""
from __future__ import annotations

import logging
import re
from typing import Callable

log = logging.getLogger("jarvis.intents")


class Intent:
    __slots__ = ("pattern", "tool", "args", "reply", "guard")

    def __init__(self, pattern: str, tool: str,
                 args: Callable | None = None, reply: str = "",
                 guard: Callable | None = None):
        self.pattern = re.compile(pattern, re.I)
        self.tool = tool
        self.args = args or (lambda m: {})
        self.reply = reply          # empty means: speak the tool's own output
        self.guard = guard          # optional veto after the pattern matches


# ── argument builders ─────────────────────────────────────────────
def _volume_args(m: re.Match) -> dict:
    return {"level": max(0, min(100, int(m.group("level"))))}


def _remember_args(m: re.Match) -> dict:
    fact = m.group("fact").strip().rstrip(".")
    return {"fact": fact[0].upper() + fact[1:] if fact else fact,
            "category": "preference"}


def _timer_args(m: re.Match) -> dict:
    from ..tools.timers import parse_duration

    duration = m.group("dur").strip(" ,.")
    label = ""
    if "label" in m.groupdict() and m.group("label"):
        label = m.group("label").strip(" ,.")
    return {"duration": duration, "label": label} if parse_duration(duration) else {}


def _timer_guard(m: re.Match) -> bool:
    """Only claim the turn if the duration is actually parseable."""
    from ..tools.timers import parse_duration

    return bool(parse_duration(m.group("dur").strip(" ,.")))


def _timer_pending(m: "re.Match") -> bool:
    """Only read "cancel it" as the timer when a timer is actually running.

    "Cancel it" is meaningless on its own and the model treated it that way:
    it called nothing, said nothing at all, and the timer kept running. With
    a countdown in progress the phrase is unambiguous; without one it should
    fall through and let the model ask what he means.
    """
    try:
        from ..tools.timers import pending_descriptions

        return bool(pending_descriptions())
    except Exception:
        return False


def _battery_level(m: "re.Match") -> dict:
    level = m.groupdict().get("level")
    direction = "below" if (m.groupdict().get("dir") or "") else "at"
    return {"percent": int(level) if level else 100, "direction": direction}


def _process_name(m: "re.Match") -> dict:
    return {"name": m.group("what").strip(" ,.")}


def _quiet_active(m: "re.Match") -> bool:
    """Only treat a greeting as "end quiet hours" if they are running.

    Otherwise "good morning" to a JARVIS who was never quiet gets answered
    with "I was not being quiet", which is a strange thing to say to someone
    saying good morning. Unguarded it falls through and he simply greets back.
    """
    try:
        from .. import quiet

        return quiet.active()
    except Exception:
        return False


def _tomorrow_args(m: "re.Match") -> dict:
    from ..tools.web import last_weather_place

    return {"location": last_weather_place(), "when": "tomorrow"}


def _weather_recent(m: "re.Match") -> bool:
    """Only read a bare "and tomorrow?" as weather if weather was just asked."""
    from ..tools.web import last_weather_place

    return bool(last_weather_place())


def _protocol_exists(name: str) -> bool:
    """Only claim phrases like "work mode" when that protocol is real.

    Without this the pattern would swallow anything shaped like a couple of
    words -- "aeroplane mode", "engage warp drive" -- and answer with a dead
    end instead of letting the model deal with it. Imported here rather than
    at module scope because the tools package imports this one.
    """
    try:
        from ..tools.protocols import exists

        return exists(name)
    except Exception:
        return False


def _component(name: str) -> Callable:
    return lambda m: {"component": name}


# ── the table. First match wins, so specific precedes general. ────
INTENTS: list[Intent] = [
    # ══ timers ══ instant, and the reason this layer exists
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:set|start|create|make|put on)\s+"
           r"(?:an?\s+)?(?:timer|countdown)\s+(?:for|of)?\s*(?P<dur>.+?)"
           r"(?:\s+for\s+(?:the\s+)?(?P<label>[\w\s]+?))?\s*[.!]?$",
           "set_timer", _timer_args, guard=_timer_guard),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:set|start|make)?\s*(?:an?\s+)?"
           r"(?P<dur>[\w\s.-]+?)\s+(?:timer|countdown)\s*[.!]?$",
           "set_timer", _timer_args, guard=_timer_guard),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:remind|wake|nudge)\s+me\s+in\s+"
           r"(?P<dur>.+?)\s*[.!]?$",
           "set_timer", _timer_args, guard=_timer_guard),
    Intent(r"^(?:jarvis[,\s]+)?(?:cancel|stop|clear)\s+(?:the\s+|my\s+)?"
           r"(?:timer|countdown)s?\s*[.!]?$", "cancel_timer"),
    Intent(r"^(?:jarvis[,\s]+)?(?:how\s+(?:long|much\s+time)\s+(?:is\s+)?"
           r"(?:left|remaining)|what\s+timers).*$", "list_timers"),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:cancel|stop|clear|forget|kill)\s+(?:it|that|those)(?:\s+please)?\s*[.!]?$",
           "cancel_timer", guard=_timer_pending),

    # ══ system readings ══ answered from the tool, never paraphrased
    Intent(r"^(?:jarvis[,\s]+)?(?:what(?:'s| is)\s+)?(?:my\s+|the\s+)?"
           r"(?:cpu|processor)(?:\s+(?:usage|load|at|doing))?\s*[.?!]?$",
           "get_system_stats", _component("cpu")),
    Intent(r"^(?:jarvis[,\s]+)?(?:how\s+much\s+|what(?:'s| is)\s+(?:my\s+)?)"
           r"(?:ram|memory)(?:\s+(?:am\s+i\s+)?(?:usage|using|used|at|free))?"
           r"\s*[.?!]?$", "get_system_stats", _component("memory")),
    Intent(r"^(?:jarvis[,\s]+)?(?:how\s+much\s+|what(?:'s| is)\s+my\s+)?"
           r"(?:disk|storage|drive|hard\s+drive)(?:\s+space)?"
           r"(?:\s+do\s+i\s+have)?(?:\s+is)?(?:\s+(?:left|free|available))?"
           r"\s*[.?!]?$", "get_system_stats", _component("disk")),
    Intent(r"^(?:jarvis[,\s]+)?(?:what(?:'s| is)\s+)?(?:my\s+|the\s+)?"
           r"battery(?:\s+(?:level|percentage|at|life|status))?\s*[.?!]?$",
           "get_battery"),
    Intent(r"^(?:jarvis[,\s]+)?(?:am\s+i\s+)?(?:charging|plugged\s+in)"
           r"\s*[.?!]?$", "get_battery"),
    Intent(r"^(?:jarvis[,\s]+)?(?:what(?:'s| is)\s+)?(?:my\s+)?system\s+"
           r"(?:status|stats|info)\s*[.?!]?$",
           "get_system_stats", _component("all")),
    Intent(r"^(?:jarvis[,\s]+)?what\s+time\s+is\s+it\s*[.?!]?$", "get_time"),
    Intent(r"^(?:jarvis[,\s]+)?what(?:'s| is)\s+(?:today(?:'s)?\s+)?the\s+date"
           r"\s*[.?!]?$", "get_time"),

    # ══ media transport ══
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?pause(?:\s+(?:the\s+)?"
           r"(?:music|song|track|audio|video|playback|it))?\s*[.!]?$",
           "pause_media", reply="Paused."),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:resume|unpause|continue)"
           r"(?:\s+(?:the\s+)?(?:music|song|track|playback|it))?\s*[.!]?$",
           "resume_media", reply="Playing."),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:skip|next)"
           r"(?:\s+(?:this|the)?\s*(?:track|song|one))?\s*[.!]?$",
           "next_track", reply="Skipped."),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:previous|go back|last)"
           r"(?:\s+(?:track|song|one))?\s*[.!]?$",
           "previous_track", reply="Going back."),
    Intent(r"^(?:jarvis[,\s]+)?what(?:'s| is)\s+(?:currently\s+)?playing"
           r"\s*[.?!]?$", "now_playing"),

    # ══ playing music ══ the desktop app, no account link
    # Ordered after pause/resume so "resume" still means resume. The last
    # pattern is deliberately broad -- "play <anything>" is unambiguous once
    # the transport commands above have had their chance.
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:play|put\s+on|start)"
           r"(?:\s+(?:some|the))?\s+music\s*[.!]?$", "play_music"),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?play\s*[.!]?$", "play_music"),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:play|put\s+on|listen\s+to)"
           r"\s+(?:some\s+)?(?P<q>.+?)(?:\s+on\s+spotify)?\s*[.!?]?$",
           "play_music", lambda m: {"query": m.group("q").strip()}),

    # ══ audio ══
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:set |turn )?(?:the )?volume"
           r"(?:\s+(?:to|at))?\s+(?P<level>\d{1,3})\s*(?:percent)?\s*[.!]?$",
           "set_volume", _volume_args),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?mute\s*(?:the\s+)?"
           r"(?:audio|sound|volume)?\s*[.!]?$",
           "set_mute", lambda m: {"muted": True}, reply="Muted."),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?unmute\s*(?:the\s+)?"
           r"(?:audio|sound|volume)?\s*[.!]?$",
           "set_mute", lambda m: {"muted": False}, reply="Unmuted."),

    # ══ relative controls ══ far commoner than absolute ones
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:turn\s+(?:it\s+|the\s+volume\s+)?up|louder|volume\s+up)\s*[.!]?$",
           "adjust_volume", lambda m: {"direction": "up"}),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:turn\s+(?:it\s+|the\s+volume\s+)?down|quieter|quiet\s+down|volume\s+down)\s*[.!]?$",
           "adjust_volume", lambda m: {"direction": "down"}),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:brighter|brighten(?:\s+the\s+screen)?)\s*[.!]?$",
           "adjust_brightness", lambda m: {"direction": "up"}),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:dimmer|dim(?:\s+the\s+screen)?)\s*[.!]?$",
           "adjust_brightness", lambda m: {"direction": "down"}),

    # ══ weather ══ was 16s through the model for a single API call
    Intent(r"^(?:jarvis[,\s]+)?(?:what(?:'s| is)\s+)?(?:the\s+)?"
           r"(?:weather|forecast|temperature)"
           r"(?:\s+(?:like|outside|today|right\s+now))*\s*[.?!]?$",
           "get_weather"),
    Intent(r"^(?:jarvis[,\s]+)?(?:what(?:'s| is)\s+)?(?:the\s+)?"
           r"(?:weather|forecast|temperature)\s+(?:like\s+)?"
           r"(?:in|at|for)\s+(?P<place>[\w\s.'-]+?)\s*[.?!]?$",
           "get_weather",
           lambda m: {"location": m.group("place").strip()}),
    Intent(r"^(?:jarvis[,\s]+)?(?:is\s+it\s+)?(?:going\s+to\s+)?"
           r"rain(?:ing)?(?:\s+today)?\s*[.?!]?$", "get_weather"),

    # ══ machine facts ══
    Intent(r"^(?:jarvis[,\s]+)?(?:am\s+i\s+online|is\s+(?:the\s+)?(?:wifi|internet)\s+(?:on|working|up)|what(?:'s| is)\s+my\s+(?:wifi|network))\s*[.?!]?$", "get_network_status"),
    Intent(r"^(?:jarvis[,\s]+)?(?:what(?:'s| is)\s+(?:my\s+|the\s+)?uptime|how\s+long\s+has\s+(?:the\s+)?(?:computer|machine|it)\s+been\s+(?:on|up|running))\s*[.?!]?$", "get_uptime"),
    Intent(r"^(?:jarvis[,\s]+)?what(?:'s| is)\s+(?:using|eating|hogging)\s+(?:all\s+)?(?:my\s+|the\s+)?(?:cpu|memory|ram|resources)\s*[.?!]?$", "get_top_processes"),
    Intent(r"^(?:jarvis[,\s]+)?what\s+can\s+you\s+do\s*[.?!]?$",
           "list_capabilities"),

    # ══ notes ══
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:make\s+a\s+note|note)(?:\s+that)?\s+(?P<text>.+)$",
           "add_note", lambda m: {"text": m.group("text").strip()}),
    Intent(r"^(?:jarvis[,\s]+)?(?:read|what\s+are)\s+my\s+notes\s*[.?!]?$", "read_notes"),

    # ══ weather ══
    # A follow-up is where he fabricated most confidently: asked "what about
    # tomorrow" he relabelled today as tomorrow and was wrong by ten degrees
    # and a rainstorm. Handled here so a real forecast is fetched.
    Intent(r"^(?:jarvis[,\s]+)?(?:what(?:'s| is)\s+)?(?:the\s+)?weather\s+(?:like\s+)?tomorrow\s*[.?!]?$",
           "get_weather", lambda m: {"when": "tomorrow"}),
    Intent(r"^(?:jarvis[,\s]+)?(?:and|what\s+about|how\s+about)\s+tomorrow\s*[.?!]?$",
           "get_weather", _tomorrow_args, guard=_weather_recent),

    # ══ what he saw while you were out, and what you were doing ══
    Intent(r"^(?:jarvis[,\s]+)?what\s+did\s+i\s+miss\s*[.?!]?$",
           "what_did_i_miss"),
    Intent(r"^(?:jarvis[,\s]+)?(?:did\s+)?anything\s+(?:happen|come\s+up)\s*[.?!]?$", "what_did_i_miss"),
    Intent(r"^(?:jarvis[,\s]+)?what\s+(?:have\s+i\s+been|was\s+i)\s+(?:working\s+on|doing)(?:\s+today)?\s*[.?!]?$",
           "what_have_i_been_doing"),
    Intent(r"^(?:jarvis[,\s]+)?where\s+has\s+my\s+day\s+gone\s*[.?!]?$",
           "what_have_i_been_doing"),

    # ══ searching a particular site ══
    # He asked for an Amazon link to a specific comic and got the Amazon
    # front page, twice. These make the common phrasings unambiguous.
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:search|look)\s+(?:on\s+|in\s+)?(?P<site>amazon|youtube|ebay|reddit|wikipedia|github|imdb|netflix|etsy|walmart|spotify)\s+for\s+(?P<q>.+?)\s*[.!?]?$",
           "search_site",
           lambda m: {"site": m.group("site"), "query": m.group("q").strip()}),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:find|get|show)\s+me\s+(?P<q>.+?)\s+on\s+(?P<site>amazon|youtube|ebay|reddit|wikipedia|github|imdb|netflix|etsy|walmart|spotify)\s*[.!?]?$",
           "search_site",
           lambda m: {"site": m.group("site"), "query": m.group("q").strip()}),

    # ══ teaching him a word ══
    Intent(r"^(?:jarvis[,\s]+)?(?:the\s+word\s+is|learn\s+the\s+(?:word|name))\s+(?P<w>[\w'-]+)\s*[.!]?$",
           "learn_word", lambda m: {"word": m.group("w")}),

    # ══ himself ══
    Intent(r"^(?:jarvis[,\s]+)?how\s+long\s+have\s+you\s+been\s+(?:running|up|awake|on)\s*[.?!]?$",
           "about_yourself", lambda m: {"topic": "uptime"}),
    Intent(r"^(?:jarvis[,\s]+)?how\s+much\s+(?:memory|ram)\s+are\s+you\s+using\s*[.?!]?$",
           "about_yourself", lambda m: {"topic": "memory"}),
    Intent(r"^(?:jarvis[,\s]+)?what\s+model\s+are\s+you(?:\s+running)?\s*[.?!]?$",
           "about_yourself", lambda m: {"topic": "model"}),
    Intent(r"^(?:jarvis[,\s]+)?tell\s+me\s+about\s+yourself\s*[.?!]?$",
           "about_yourself"),

    # ══ standing watches ══
    # Ordered narrowest first. The last pattern would happily swallow the
    # download and battery phrasings, so they get their turn before it.
    Intent(r"^(?:jarvis[,\s]+)?(?:tell|let)\s+me\s+(?:know\s+)?when\s+(?:the\s+|my\s+|that\s+)?downloads?\s+(?:is\s+|has\s+)?(?:finish(?:es|ed)?|done|completes?)\s*[.!?]?$",
           "watch_for_download"),
    Intent(r"^(?:jarvis[,\s]+)?(?:tell|let)\s+me\s+(?:know\s+)?when\s+(?:it(?:'s| is)?|the\s+battery(?:\s+is)?)\s+(?:fully\s+)?(?:charged|full)\s*[.!?]?$",
           "watch_for_battery", lambda m: {"percent": 100}),
    Intent(r"^(?:jarvis[,\s]+)?(?:tell|let)\s+me\s+(?:know\s+)?when\s+(?:the\s+)?battery\s+(?:is\s+|gets\s+|reaches\s+|hits\s+|(?P<dir>drops\s+below|falls\s+below|goes\s+below)\s*)(?:at\s+)?(?P<level>\d{1,3})\s*(?:percent|%)?\s*[.!?]?$",
           "watch_for_battery", _battery_level),
    Intent(r"^(?:jarvis[,\s]+)?(?:tell|let)\s+me\s+(?:know\s+)?when\s+(?:the\s+|my\s+)?(?P<what>[a-z0-9 .-]+?)\s+(?:is\s+|has\s+)?(?:finish(?:es|ed)?|done|completes?|closes?|exits?)\s*[.!?]?$",
           "watch_for_process", _process_name),
    Intent(r"^(?:jarvis[,\s]+)?what\s+are\s+you\s+watching(?:\s+for)?\s*[.?!]?$", "list_watches"),
    Intent(r"^(?:jarvis[,\s]+)?(?:stop|cancel)\s+watching(?:\s+for)?(?:\s+everything)?\s*[.!?]?$", "cancel_watch"),

    # ══ quiet hours ══
    # Goodnight puts him away; the morning brings him back. Both are things
    # said in passing rather than commands, so the phrasings are generous.
    Intent(r"^(?:jarvis[,\s]+)?(?:good\s*night|goodnight|night)(?:[,\s]+jarvis)?\s*[.!]?$",
           "begin_quiet_hours"),
    Intent(r"^(?:jarvis[,\s]+)?(?:that is|that's)\s+it\s+for\s+(?:today|tonight|the\s+night)\s*[.!]?$",
           "begin_quiet_hours"),
    Intent(r"^(?:jarvis[,\s]+)?(?:good\s*morning|morning)(?:[,\s]+jarvis)?\s*[.!]?$",
           "end_quiet_hours", guard=_quiet_active),
    Intent(r"^(?:jarvis[,\s]+)?(?:let's|lets)\s+get\s+(?:to\s+work|started|going)\s*[.!]?$",
           "end_quiet_hours", guard=_quiet_active),
    Intent(r"^(?:jarvis[,\s]+)?(?:i'm|i am)\s+back\s*[.!]?$",
           "end_quiet_hours", guard=_quiet_active),

    # ══ snoozing one thing he keeps mentioning ══
    Intent(r"^(?:jarvis[,\s]+)?(?:stop|quit)\s+(?:telling|reminding|mentioning)\s+(?:me\s+)?(?:about\s+)?that\s*[.!]?$",
           "snooze_observation"),
    Intent(r"^(?:jarvis[,\s]+)?(?:don't|do not|dont)\s+(?:tell|mention|remind)\s+(?:me\s+)?(?:about\s+)?that\s+again\s*[.!]?$",
           "snooze_observation"),
    Intent(r"^(?:jarvis[,\s]+)?snooze\s+(?:that|it)\s*[.!]?$",
           "snooze_observation"),

    Intent(r"^(?:jarvis[,\s]+)?what(?:'s| is)\s+scheduled\s*[.?!]?$",
           "list_schedules"),
    Intent(r"^(?:jarvis[,\s]+)?(?:list|show)\s+(?:my\s+)?schedules\s*[.?!]?$", "list_schedules"),

    # ══ named protocols ══
    # "JARVIS, initiate the House Party protocol." The guard is what makes the
    # looser phrasings safe: "work mode" and "engage focus" only route here
    # when a protocol by that name actually exists, so an undefined phrase
    # falls through to the model instead of being swallowed by a dead end.
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?"
           r"(?:initiate|activate|engage|run|start|begin|execute)\s+"
           r"(?:the\s+)?(?P<name>[a-z][a-z ]*?)"
           r"(?:\s+protocol|\s+routine|\s+mode)?\s*[.!]?$",
           "run_protocol", lambda m: {"name": m.group("name").strip()},
           guard=lambda m: _protocol_exists(m.group("name"))),
    Intent(r"^(?:jarvis[,\s]+)?(?:the\s+)?(?P<name>[a-z][a-z ]*?)\s+(?:protocol|mode)\s*[.!]?$",
           "run_protocol", lambda m: {"name": m.group("name").strip()},
           guard=lambda m: _protocol_exists(m.group("name"))),
    Intent(r"^(?:jarvis[,\s]+)?(?:what|which)\s+protocols?\s+"
           r"(?:do\s+i\s+have|are\s+there|do\s+you\s+know)\s*[.?!]?$",
           "list_protocols"),
    Intent(r"^(?:jarvis[,\s]+)?(?:list|show)\s+(?:my\s+)?protocols\s*[.?!]?$",
           "list_protocols"),

    # ══ working on what he has copied ══
    # "this" means the clipboard. The tool still calls the model to do the
    # work, so the saving here is only the tool-selection round trip -- but
    # that is about a second, on a phrase he is likely to use often.
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:proofread|spell\s*check|"
           r"fix|correct)\s+(?:this|that|it)\s*[.!?]?$",
           "proofread_clipboard"),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:summarise|summarize)\s+"
           r"(?:this|that|it)\s*[.!?]?$",
           "summarise_clipboard"),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?translate\s+(?:this|that|it)\s+"
           r"(?:in)?to\s+(?P<lang>[a-z]+)\s*[.!?]?$",
           "translate_clipboard", lambda m: {"language": m.group("lang")}),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?rewrite\s+(?:this|that|it)\s+"
           r"(?:to\s+be\s+|to\s+sound\s+|so\s+it\s+is\s+)?"
           r"(?P<style>[a-z ]+?)\s*[.!?]?$",
           "rewrite_clipboard", lambda m: {"style": m.group("style").strip()}),

    # ══ what is on screen ══
    Intent(r"^(?:jarvis[,\s]+)?(?:what|which)\s+(?:page|site|website|tab)\s+"
           r"(?:am\s+i\s+on|is\s+(?:this|open)|are\s+we\s+on)\s*[.?!]?$",
           "current_page"),
    Intent(r"^(?:jarvis[,\s]+)?what\s+am\s+i\s+looking\s+at\s*[.?!]?$",
           "current_page"),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?open\s+a\s+new\s+tab\s*[.!?]?$",
           "open_new_tab"),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?close\s+(?:this\s+|the\s+)?tab"
           r"\s*[.!?]?$",
           "close_tab"),

    # ══ opening things ══
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:open|show|go to)\s+"
           r"(?:my\s+|the\s+)?(?P<folder>downloads|documents|desktop|pictures|"
           r"music|videos|screenshots)(?:\s+folder)?\s*[.!]?$",
           "open_folder", lambda m: {"name": m.group("folder")}),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?search\s+(?:on\s+)?youtube\s+"
           r"(?:for\s+)?(?P<q>.+?)\s*[.!?]?$",
           "open_youtube_search", lambda m: {"query": m.group("q").strip()}),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:get|give me)?\s*directions\s+"
           r"to\s+(?P<dest>.+?)\s*[.!?]?$",
           "get_directions", lambda m: {"destination": m.group("dest").strip()}),
    Intent(r"^(?:jarvis[,\s]+)?(?:can\s+you\s+|could\s+you\s+)?(?:please\s+)?"
           r"(?:open(?:\s+up)?|go\s+to|pull\s+up|take\s+me\s+to|bring\s+up|launch|show\s+me)"
           r"\s+(?P<site>youtube|github|gmail|google|reddit|twitter|amazon|"
           r"netflix|spotify\.com|wikipedia|linkedin|discord|twitch|ebay|espn|"
           r"chatgpt|claude|notion|figma|outlook)(?:\s+for\s+me)?\s*[.!?]?$",
           "open_website", lambda m: {"site": m.group("site")}),

    # ══ the machine itself ══ destructive, and confirmed before running
    # Deterministic on purpose. Left to the model, "shut down my computer"
    # produced a confident "shutting down" with no tool call, no confirmation
    # and no shutdown. Matching here routes it straight to the confirmation
    # gate in brain/llm.py.
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:shut\s*down|turn\s+off|"
           r"power\s+(?:down|off))\s+(?:my\s+|the\s+)?"
           r"(?:computer|pc|laptop|machine|system)\s*[.!]?$",
           "shutdown_computer", lambda m: {"restart": False}),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:restart|reboot)\s+"
           r"(?:my\s+|the\s+)?(?:computer|pc|laptop|machine|system)"
           r"\s*[.!]?$",
           "shutdown_computer", lambda m: {"restart": True}),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:put\s+)?(?:my\s+|the\s+)?"
           r"(?:computer|pc|laptop|machine)\s+(?:to\s+sleep|asleep)"
           r"\s*[.!]?$", "sleep_computer"),

    # ══ machine ══
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?lock\s+(?:the\s+|my\s+)?"
           r"(?:screen|computer|pc|laptop|workstation)\s*[.!]?$",
           "lock_screen", reply="Locking now."),
    # Destination taken from the sentence, not from the model. Asked to save
    # a screenshot to the Desktop, the model simply never passed a location
    # and then announced the Desktop anyway.
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:take|grab|get)\s+a\s+screenshot\s+(?:and\s+)?(?:save\s+it\s+)?(?:to|on|in)\s+(?:my\s+|the\s+)?(?P<where>desktop|documents|downloads|pictures)(?:\s+folder)?\s*[.!]?$",
           "take_screenshot",
           lambda m: {"location": m.group("where")}),
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:take|grab|get)\s+a\s+screenshot\s*[.!]?$", "take_screenshot"),

    # Same for exporting the conversation.
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:save|export|make|create)\s+(?:a\s+)?(?:pdf\s+)?(?:of\s+)?(?:our\s+|the\s+|this\s+)?conversation\s*(?:as\s+a\s+pdf\s*)?(?:and\s+)?(?:save\s+it\s+)?(?:to|on|in)\s+(?:my\s+|the\s+)?(?P<where>desktop|documents|downloads)(?:\s+folder)?\s*[.!]?$",
           "export_conversation",
           lambda m: {"location": m.group("where")}),

    # ══ memory ══
    Intent(r"^(?:jarvis[,\s]+)?(?:please\s+)?remember(?:\s+that)?\s+"
           r"(?P<fact>.+)$", "remember", _remember_args),
]


def match(text: str) -> tuple[str, dict, str] | None:
    """Return (tool, arguments, canned_reply) for an unambiguous command.

    An empty canned_reply means the tool's own output is the answer, which is
    what keeps readings accurate -- the model never gets a chance to reword a
    measurement into something untrue.
    """
    stripped = text.strip()
    for intent in INTENTS:
        m = intent.pattern.match(stripped)
        if not m:
            continue
        if intent.guard and not intent.guard(m):
            continue        # matched the shape but not the substance
        try:
            args = intent.args(m)
        except Exception:
            log.exception("intent arguments failed for %r", stripped)
            return None
        log.info("intent: %r -> %s(%s)", stripped, intent.tool, args)
        return intent.tool, args, intent.reply
    return None
