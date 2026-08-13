# Everything JARVIS can do

Say **"Hey JARVIS"**, wait for the chime, then speak. Or press **Ctrl+Alt+J**
from any application, **Space** when the window has focus, or just type in the
box at the bottom.

Phrasing is flexible — these are examples, not magic words. Anything in the
**Instant** column bypasses the language model entirely and runs in well under a
second.

---

## Controls

| | |
|---|---|
| **"Hey JARVIS"** | Wake word |
| **Ctrl+Alt+J** | Wake him from any app, no speaking required |
| **Space** | Push to talk (when the window has focus) |
| **F11** | Full screen on/off |
| **Esc** | Cut him off mid-sentence |
| **Talk / Stop / Mic** buttons | Same three things, by mouse |
| Talking over him | Interrupts immediately |

---

## Instant commands

These are matched in code, so they always work and never wait on the model.

| Say | What happens |
|---|---|
| "Pause" · "Pause the music" | Pauses whatever is playing |
| "Resume" · "Unpause" | Resumes |
| "Skip" · "Next" · "Skip this track" | Next track |
| "Previous" · "Go back" | Previous track |
| "What's playing?" | Reports the current track |
| "Volume 40" · "Set volume to 70" | Sets volume |
| "Mute" · "Unmute" | Mutes system audio |
| "Lock my screen" | Locks Windows |
| "Take a screenshot" | Saves to Pictures\JARVIS |
| "Remember that ..." | Stores a fact permanently |

---

## System

| Ask | Example |
|---|---|
| System status | *"How's my system doing?"* · *"What's my CPU at?"* |
| Battery | *"How much battery do I have?"* · *"Am I charging?"* |
| Memory / disk | *"How much RAM am I using?"* · *"How much disk space is left?"* |
| Time and date | *"What time is it?"* · *"What's today's date?"* |
| Open apps | *"Open Spotify"* · *"Launch VS Code"* · *"Open Brave"* |
| Close apps | *"Close Notepad"* · *"Quit Spotify"* |
| Windows | *"What have I got open?"* · *"Switch to Brave"* |
| Volume | *"Turn it down"* · *"What's the volume?"* |
| Brightness | *"Set brightness to 50"* · *"Dim the screen"* |
| Clipboard | *"What's on my clipboard?"* · *"Copy this to my clipboard: ..."* |
| Screenshot | *"Take a screenshot"* |
| Power | *"Lock the screen"* · *"Go to sleep"* · *"Restart"* |

He knows these app names out of the box: Spotify, VS Code, Brave, Chrome,
Explorer, Task Manager, Terminal, PowerShell, Notepad, Calculator, Settings,
Paint, Snipping Tool, Camera. Anything else he looks up in the Start Menu, so
*"Open Steam"* works if Steam is installed.

---

## Web

Needs internet. Everything else on this page works offline.

| Ask | Example |
|---|---|
| Search | *"Search the web for the new Ryzen chips"* · *"Look up X"* |
| Current events | *"What's happening with AMD?"* |
| Read a page | *"Read me that article at ..."* · *"Summarise this URL: ..."* |
| Weather | *"What's the weather?"* · *"What's the forecast in Boston?"* |
| News | *"Any news today?"* · *"Give me the tech headlines"* |

Weather uses your location automatically if you don't name a city.

---

## Files

Searches your Documents, Desktop and Downloads.

| Ask | Example |
|---|---|
| Find | *"Find my resume"* · *"Where's that config file?"* |
| Read | *"Read me the top of my notes file"* |
| Recent | *"What have I been working on?"* · *"Show me recent files"* |
| Open | *"Open my budget spreadsheet"* |

---

## Media

Works with Spotify, browsers, any player — through the same channel as your
keyboard's media keys. No account linking, no API key.

| Ask | Example |
|---|---|
| Transport | *"Pause"* · *"Skip"* · *"Go back"* · *"Resume"* |
| Now playing | *"What's this song?"* · *"What's playing?"* |
| Find music | *"Find Led Zeppelin on Spotify"* (opens the search) |

**One real limit:** he cannot start a *specific* song by name. SMTC has no such
call, and doing it properly needs a Spotify account link. *"Play Back in Black"*
opens the search, ready to hit play.

---

## Memory

Persists in SQLite, survives restarts and reboots.

| Ask | Example |
|---|---|
| Remember | *"Remember that I use Brave as my main browser"* |
| | *"Remember my sister's birthday is in March"* |
| Recall | *"What's my main browser?"* · *"What do you know about me?"* |
| Forget | *"Forget what I said about the browser"* |

Memory is a tool he *calls*, not context he's fed — that's what keeps replies at
two seconds. Occasionally he'll need a direct question (*"what do you know about
X"*) rather than an oblique one.

---

## Things that ask first

These require you to say **yes** out loud before they run:

- *"Shut down the computer"* · *"Restart"*
- *"Go to sleep"*
- *"Run this PowerShell command: ..."*

He'll ask, then wait. Say *"yes"*, *"do it"*, or *"go ahead"* — or *"cancel"* /
*"never mind"* to drop it. Say *"cancel shutdown"* to stop a countdown already
running.

---

## Chaining

He can do several things in one breath:

> *"What's my battery at, pause the music, and tell me the weather"*

> *"Take a screenshot and tell me how much disk space I've got left"*

---

## Reading the reactor

The interface tells you what he's doing without you reading a word:

| Look | State |
|---|---|
| Slow cyan breathing | Idle, listening for the wake word |
| Teal, ring reacting to your voice | Listening to you |
| Blue, fast counter-rotation, particles pulling in | Thinking |
| Violet, a scanner sweeping the dial | Running a tool |
| **Gold, pulsing to his voice** | Speaking |
| Red | Something went wrong |

The **SYSTEM ACTIVITY** panel logs every tool call as it happens, so nothing he
does is hidden.

---

## Worth testing first

A quick pass that touches every subsystem:

1. *"Hey JARVIS, what's my battery at?"* — wake word, tools, voice
2. *"Pause the music"* with Spotify playing — instant path
3. *"Remember that my favourite film is Iron Man"* — then restart the app and
   ask *"what's my favourite film?"* — persistence
4. **Turn off Wi-Fi**, then *"what's my CPU usage?"* — proves it's fully local
5. Wi-Fi back on: *"search the web for Ryzen AI news"*
6. Interrupt him mid-sentence — barge-in
7. **F11** — full screen

---

## If something misbehaves

`logs\jarvis.log` records every stage. Useful lines:

- `wake word fired (0.87)` — he heard you
- `woke but heard no speech in 6.0s` — he woke but the microphone gave him
  nothing usable
- `heard: ...` — what he actually transcribed
- `tool get_battery({}) -> ...` — the tool and its real result

If he wakes too easily or not easily enough, change `wake.threshold` in
`config.yaml` — lower catches more, higher rejects more.
