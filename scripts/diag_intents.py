import sys; sys.path.insert(0, r"C:\Users\nicho\Documents\CodingProjects\Jarvis")
from jarvis.brain import intents
CASES = [
    ("set a timer for 10 seconds", "set_timer"),
    ("10 second timer", "set_timer"),
    ("20 minute timer", "set_timer"),
    ("8 hour timer", "set_timer"),
    ("set a timer for half an hour", "set_timer"),
    ("remind me in 5 minutes", "set_timer"),
    ("cancel the timer", "cancel_timer"),
    ("what's my cpu", "get_system_stats"),
    ("what is my CPU usage", "get_system_stats"),
    ("how much memory am I using", "get_system_stats"),
    ("how much disk space do I have left", "get_system_stats"),
    ("what's my battery", "get_battery"),
    ("am I charging", "get_battery"),
    ("what time is it", "get_time"),
    ("pause the music", "pause_media"),
    ("open youtube", "open_website"),
    ("go to youtube", "open_website"),
    ("open my downloads", "open_folder"),
    ("open downloads folder", "open_folder"),
    # search_site now claims this. Verified to build a byte-identical
    # URL to open_youtube_search, so the outcome is unchanged and the
    # general mechanism handling it is the better answer.
    ("search youtube for iron man", "search_site"),
    ("directions to Boston", "get_directions"),
    ("volume 40", "set_volume"),
    ("lock my screen", "lock_screen"),
    ("remember that I like tea", "remember"),
    ("system status", "get_system_stats"),
    # must NOT match -- these need the model
    ("explain quantum computing", None),
    ("create a pdf of our conversation", None),
    ("what's the weather in Boston", "get_weather"),
    ("set a timer for bananas", None),
    ("how are you", None),
    ("open a discussion about timers", None),
]
bad = 0
for text, want in CASES:
    got = intents.match(text)
    name = got[0] if got else None
    ok = name == want
    bad += (not ok)
    args = got[1] if got else ""
    print(f"{'ok  ' if ok else 'FAIL'} {text[:38]:40s} -> {str(name):20s} {args if ok and args else ''}")
print(f"\nfailures: {bad}/{len(CASES)}")
