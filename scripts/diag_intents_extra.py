import sys
sys.path.insert(0, r"C:\Users\nicho\Documents\CodingProjects\Jarvis")
from jarvis.brain import intents
CASES = [
    ("turn it up", "adjust_volume"), ("louder", "adjust_volume"),
    ("turn it down", "adjust_volume"), ("quieter", "adjust_volume"),
    ("volume up", "adjust_volume"), ("brighter", "adjust_brightness"),
    ("dim the screen", "adjust_brightness"),
    ("am I online", "get_network_status"),
    ("is the wifi working", "get_network_status"),
    ("what's my uptime", "get_uptime"),
    ("what's using all my cpu", "get_top_processes"),
    ("what can you do", "list_capabilities"),
    ("note that the router needs rebooting", "add_note"),
    ("read my notes", "read_notes"),
    # regressions
    ("10 second timer", "set_timer"), ("what is my battery", "get_battery"),
    ("what is my cpu", "get_system_stats"), ("pause", "pause_media"),
    ("open youtube", "open_website"), ("volume 40", "set_volume"),
    # must still fall through
    ("explain quantum computing", None),
    ("what's the weather in Boston", "get_weather"),
    ("create a pdf of our conversation", None),
]
bad = 0
for text, want in CASES:
    got = intents.match(text)
    name = got[0] if got else None
    ok = name == want
    bad += (not ok)
    print(f"{'ok  ' if ok else 'FAIL'} {text[:36]:38s} -> {name}")
print(f"\nfailures: {bad}/{len(CASES)}")
