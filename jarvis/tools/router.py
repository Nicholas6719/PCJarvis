"""Tool selection.

This exists because of one measurement. On this machine Ollama re-evaluates the
whole prompt whenever its prefix changes, at ~90-150 tokens/second, but replays
an *unchanged* prefix from the KV cache at ~3,000 tokens/second. The tool
schemas sit in that prefix. So the question is not "how few tools can we send"
but "can we send the same ones every time".

Measured across a five-turn conversation on qwen2.5:7b, GPU-resident:

    all 37 tools, stable      1.9s/turn   but 0/5 tool calls -- too many
                                          options and it stops choosing at all
    10 tools, re-routed       8.6s/turn   5/5 tool calls, cache missed every
                                          turn because the set kept changing
    22 tools, stable          2.3s/turn   5/5 tool calls

So: a fixed core set, always sent, always in the same order. Rare tools are
admitted only on an unambiguous keyword, which costs one cache miss on the turn
that needs them -- a fair price for a tool used once a month.

The ordering is deliberately deterministic. A set that reorders between turns
is a different prefix, and a different prefix is a cache miss.
"""
from __future__ import annotations

import logging
import re

from .registry import REGISTRY

log = logging.getLogger("jarvis.tools.router")

# Always sent, always in this order. Covers everything he asks for day to day.
CORE: tuple[str, ...] = (
    # system
    "get_time", "get_battery", "get_system_stats", "get_volume", "set_volume",
    "open_app", "close_app", "take_screenshot", "lock_screen",
    # web
    "web_search", "get_weather", "get_news", "read_webpage",
    # files
    "find_files", "read_file", "open_file",
    # media
    "play_music", "play_pause", "pause_media", "next_track", "now_playing",
    # memory
    "remember", "recall",
)

# Everything else, admitted only on an unambiguous word. Each entry costs a
# cache miss on the turn it appears, so the triggers are kept tight.
EXTRAS: dict[str, set[str]] = {
    "set_brightness":      {"brightness", "dim", "brighter", "dimmer"},
    "set_mute":            {"mute", "unmute", "silence"},
    "resume_media":        {"resume", "unpause"},
    "previous_track":      {"previous", "back", "rewind", "replay"},
    "open_spotify_search": {"spotify", "playlist", "album"},
    "list_running_apps":   {"running", "open apps", "what's open"},
    "focus_window":        {"focus", "switch", "bring up", "foreground"},
    "read_clipboard":      {"clipboard", "copied"},
    "write_clipboard":     {"clipboard", "copy"},
    "list_recent_files":   {"recent", "lately", "latest files"},
    "forget":              {"forget", "delete that", "remove that"},
    "cancel_shutdown":     {"cancel"},
    "sleep_computer":      {"sleep", "suspend", "hibernate"},
    "shutdown_computer":   {"shutdown", "shut down", "restart", "reboot"},
    "run_command":         {"powershell", "command line", "run command",
                            "terminal command", "script"},

    # ── documents ──
    "take_screenshot":     {"desktop", "downloads", "save it to"},
    "export_conversation": {"pdf", "export", "transcript", "write up",
                            "save our conversation", "save this conversation",
                            "document of our", "our conversation"},
    "create_pdf":          {"pdf", "document", "write a document"},
    "save_text_file":      {"save to a file", "text file", "save this as",
                            "write a file"},
    "list_documents":      {"documents you", "what have you created",
                            "my documents folder"},

    # ── browser & navigation ──
    "open_website":        {"website", "site", "youtube", "github", "reddit",
                            "gmail", "browser", "go to", "pull up", "open up"},
    "search_in_browser":   {"show me", "pull up", "browse", "look up online"},
    "open_youtube_search": {"youtube"},
    "get_directions":      {"directions", "navigate", "route", "how do i get",
                            "take me to", "map"},
    "open_folder":         {"folder", "downloads", "explorer", "my documents",
                            "desktop folder", "pictures"},

    # ── timers ──
    "set_timer":           {"timer", "remind me in", "countdown", "alarm",
                            "wake me in", "give me"},
    "list_timers":         {"timers", "how long left", "time remaining"},
    "cancel_timer":        {"cancel the timer", "stop the timer",
                            "cancel timer", "forget the timer"},

    # ── relative controls, machine facts, notes, help ──
    "adjust_volume":       {"louder", "quieter", "turn it up",
                            "turn it down", "turn up", "turn down"},
    "adjust_brightness":   {"brighter", "dimmer", "dim"},
    "get_network_status":  {"wifi", "internet", "online", "network",
                            "connected"},
    "get_uptime":          {"uptime", "how long has", "since i restarted",
                            "been running"},
    "get_top_processes":   {"using the most", "heaviest", "what is using",
                            "hogging", "top processes"},
    "add_note":            {"note that", "make a note", "jot"},
    "read_notes":          {"my notes", "read my notes", "what notes"},
    "list_capabilities":   {"what can you do", "capabilities", "help me",
                            "what do you do", "your abilities"},
    "get_time_until":      {"how long until", "time until"},

    "get_trend":           {"has been", "this week", "usual", "lately",
                            "trend", "over time", "compared to"},

    # -- things he is asked to keep an eye on --
    "watch_for_process":   {"tell me when", "let me know when", "when it is done",
                            "when the build", "when it finishes"},
    "watch_for_battery":   {"when it is charged", "when the battery",
                            "fully charged", "when it hits"},
    "watch_for_download":  {"when the download", "when my download"},
    "list_watches":        {"what are you watching", "watching for"},
    "cancel_watch":        {"stop watching", "cancel watching"},

    # -- holding his tongue --
    "begin_quiet_hours":   {"quiet hours", "goodnight", "good night"},
    "end_quiet_hours":     {"good morning", "get to work", "i am back"},
    "snooze_observation":  {"stop telling me", "snooze", "mention that"},
    "clear_snoozes":       {"unsnooze", "clear snoozes"},

    # -- named routines, the House Party pattern --
    "run_protocol":        {"protocol", "initiate", "engage", "mode"},
    "list_protocols":      {"protocols", "what protocols", "my routines"},
    "create_protocol":     {"create a protocol", "new protocol",
                            "define a protocol", "make a protocol"},
    "delete_protocol":     {"delete the protocol", "remove the protocol",
                            "forget the protocol"},

    # -- what is on screen, read from the window title --
    "current_page":        {"what page", "what site", "what website",
                            "what am i looking at", "this page",
                            "which page", "what tab"},
    "open_new_tab":        {"new tab"},
    "close_tab":           {"close tab", "close this tab",
                            "close the tab"},

    # -- working on copied text, all on-device --
    # "this" almost always means the clipboard when he asks for one of
    # these, so the triggers are the verbs rather than the word clipboard.
    "proofread_clipboard": {"proofread", "fix this", "correct this",
                            "check my spelling", "spellcheck", "grammar"},
    "rewrite_clipboard":   {"rewrite", "reword", "rephrase", "make this",
                            "more formal", "more professional",
                            "friendlier", "shorter"},
    "summarise_clipboard": {"summarise", "summarize", "summary", "gist",
                            "what does this say", "tldr"},
    "translate_clipboard": {"translate", "in spanish", "in french",
                            "in german", "in italian", "in japanese"},

    # -- spotify, driven through the desktop app --
    "play_music":          {"play", "put on", "listen to", "music",
                            "song", "spotify"},
    "play_playlist":       {"playlist", "mix", "radio"},
    "open_spotify":        {"spotify"},
}

_WORD = re.compile(r"[a-z']+")


def select(query: str, limit: int | None = None) -> list[dict]:
    """Schemas to offer for this utterance: the stable core, plus any extras
    the wording clearly asks for."""
    names = list(CORE)

    lowered = query.lower()
    words = set(_WORD.findall(lowered))
    for name, triggers in EXTRAS.items():
        if name in names or name not in REGISTRY:
            continue
        # Multi-word triggers are substring matches; single words must be whole
        # words, so "back" in "background" does not summon previous_track.
        if any((t in lowered) if " " in t else (t in words) for t in triggers):
            names.append(name)

    schemas = [REGISTRY[n].schema for n in names if n in REGISTRY]
    if limit and len(schemas) > limit:
        schemas = schemas[:limit]

    if len(names) > len(CORE):
        log.debug("admitted extras: %s", names[len(CORE):])
    return schemas



def warm_prefix_query() -> str:
    """A query that selects exactly the core set, for warming the KV cache at
    startup so the first real question is not the one that pays for it."""
    return "hello"
