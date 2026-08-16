# Everything JARVIS can do

Say **"Jarvis"** or **"Hey Jarvis"**, then speak. After he answers you can simply
keep talking for 15 seconds — no wake word. **Ctrl+Alt+J** from any application,
**Space** when the window has focus, or type in the box.

**Bold** commands are *instant* — they never touch the language model, so they
answer in well under a second and their readings cannot be reworded into
something untrue.

---

## Controls

| | |
|---|---|
| "Jarvis" / "Hey Jarvis" | Wake word |
| Ctrl+Alt+J | Wake from any app |
| Space | Push to talk |
| F11 | Full screen |
| Esc | Cut him off |
| Say "Jarvis" over him | Interrupts mid-sentence |
| "That's all" / "go to sleep" | Stand down — minimises, still listening |
| "Jarvis, shut down" | Close JARVIS entirely |

---

## Standing down vs shutting down

Three different things, and he tells them apart:

| Say | What happens |
|---|---|
| **"That's all"** · **"go to sleep"** · **"return to wake mode"** | Minimises, stays listening. Say "Jarvis" to bring him back full screen. |
| **"Jarvis, shut down"** · "exit" · "quit" · "power down" | Closes the application. |
| *"Shut down my computer"* | Shuts down the **laptop** — asks you to confirm first. |

Courtesy in front is fine and expected:

*"thank you, go to sleep"* · *"that's all, go to sleep"* · *"good work, go to
sleep"* · *"thank you, return to wake mode"* · *"nice work, shut down"*

The reactor shows which happened: **dim blue and barely breathing** when asleep,
**collapsing inward** when shutting down.

---

## Instant commands

### Timers
**"10 second timer"** · **"20 minute timer"** · **"set a timer for half an hour"**
· **"remind me in 5 minutes"** · **"how long is left?"** · **"cancel the timer"**

He speaks up when it elapses, and waits politely if he's mid-sentence.

### System
**"What's my battery?"** · **"am I charging?"** · **"what's my CPU?"** ·
**"how much memory am I using?"** · **"how much disk space is left?"** ·
**"system status"** · **"what time is it?"** · **"what's my uptime?"** ·
**"am I online?"** · **"what's using all my CPU?"**

Each answers *only* what was asked — the CPU question gives you the CPU.

### Audio and screen
**"Turn it up"** · **"turn it down"** · **"louder"** · **"quieter"** ·
**"volume 40"** · **"mute"** · **"unmute"** · **"brighter"** · **"dimmer"** ·
**"take a screenshot"** · **"lock my screen"**

### Music
**"Play music"** · **"play some jazz"** · **"play Take Five by Dave Brubeck"** ·
**"listen to Miles Davis"** · **"pause"** · **"resume"** · **"skip"** ·
**"previous"** · **"what's playing?"**

Uses your Spotify app directly — no account link, no API key. He finds the
track and hands it to the player. **He verifies what actually started and
tells you the truth**: if a playlist opens but does not begin, he says so
rather than claiming it is playing.

### Opening things
**"Open YouTube"** · **"go to GitHub"** · **"open my downloads"** ·
**"search YouTube for Iron Man"** · **"directions to Boston"**

Anything opened is brought to the front.

### Weather
**"What's the weather?"** · **"what's the weather in Boston?"** ·
**"is it going to rain?"**

### Memory and notes
**"Remember that I use Brave"** · **"note that the router needs rebooting"** ·
**"read my notes"** · **"what can you do?"**

Memory is for facts about you that shape later answers. Notes are jottings you
read back.

Everything he remembers lives in `Documents\JARVIS\Memory` as one markdown
file per fact. **Delete a file and he forgets it. Edit one and he believes the
new version. Drop one in and he learns it** — no restart needed. Obsidian is
installed; point it at that folder and you get search and a graph over
everything he knows. Nothing requires it.

### Protocols
**"initiate the work protocol"** · **"work mode"** · **"run good night"** ·
**"list my protocols"**

A protocol is a name for a list of actions. Two come defined: **work**
(pause music, volume down, report memory) and **good night** (pause music,
dim the screen, lock up). The wording is forgiving — "work", "work mode"
and "the work protocol" are the same thing.

Ask him to create one and he will, or edit `data/protocols.json` directly.
He refuses to put anything irreversible in a protocol — no shutdown, no
shell commands — because one misheard word should not be able to do real
damage. Ask for those directly and he confirms first.

### Things he says without being asked
He now speaks up on his own: a battery running down, a disk filling, memory
under pressure, something pinning the processor for minutes, a download
finishing, and how long you have been sitting there.

The thresholds are high on purpose and each one only speaks when a limit is
*crossed*, so a value sitting near the line does not produce a running
commentary. If you have dismissed him, only a critical battery gets through.
All of it is tunable under `watch:` in `config.yaml`; set `enabled: false`
to switch it off entirely.

He also mentions things before doing what you asked — a timer still running
when you shut down, unsaved work in an editor. He says it once and then does
as he was told.

### Asking him to watch for something
**"tell me when the download finishes"** · **"tell me when it's charged"** ·
**"tell me when the battery hits 80"** · **"let me know when handbrake
finishes"** · **"what are you watching for?"** · **"stop watching"**

These outlive the conversation. He keeps them across a restart and tells you
when they happen, however long that takes. A program watch waits until it has
actually seen the thing running, so asking before you start a build works.
They fire once and forget themselves, and anything uncollected after a week
is dropped.

### Quiet hours
**"goodnight Jarvis"** silences him. **"good morning"**, **"let's get to
work"** or **"I'm back"** brings him back.

Both are silent — he does not announce either, and saying good morning when he
was never quiet just gets a greeting. Urgent things still get through, and it
lapses on its own after twelve hours so forgetting to say good morning cannot
mute him permanently.

If one particular remark gets tiresome: **"stop telling me about that"** or
**"don't mention that again"** silences that one thing for eight hours and
leaves everything else alone.

### How things have been
**"how has my memory been this week?"** · **"has the CPU been busier than
usual?"** · **"how has the battery been?"**

He samples the machine every minute and keeps a fortnight, so these answer
with a comparison rather than another reading. If he has nothing to compare
against yet, he says so rather than implying a trend.

### Text you have copied
**"proofread this"** · **"fix this"** · **"rewrite this to be more formal"** ·
**"summarise this"** · **"translate this into Spanish"**

"This" always means the clipboard. Copy something first, then ask. The result
goes straight back onto your clipboard so you can paste it — except a summary,
which is spoken instead, since a summary you have to paste to read defeats the
point.

All of it runs on the local model. Nothing you copy leaves the machine, which
matters more here than anywhere else: the clipboard is where passwords and
half-written messages live.

### What is on screen
**"what page am I on?"** · **"what am I looking at?"** · **"open a new tab"** ·
**"close this tab"**

Read from the window title, so it needs no browser extension and works with
Chrome, Edge, Firefox and Brave. If the browser is not in front he says so
rather than guessing. Closing a tab refuses outright unless a browser is
actually focused — Ctrl+W in the wrong window closes a document, not a tab —
and he tells you Ctrl+Shift+T brings it back.

---

## Through the model

Slower (2–5s), because they need actual thinking:

| Ask | Example |
|---|---|
| Web search | *"Search the web for the new Ryzen chips"* |
| Read a page | *"Summarise this article: ..."* |
| News | *"Any tech news today?"* |
| Files | *"Find my resume"* · *"what have I been working on?"* |
| Documents | *"Create a PDF of our conversation on my desktop"* |
| Recall | *"What's my main browser?"* |
| Apps | *"Open Spotify"* · *"close Notepad"* |
| Clipboard | *"What's on my clipboard?"* |
| Anything else | *"Explain quantum computing"* |

---

## Documents

*"Create a PDF of our conversation"* · *"...and put it on my desktop"* ·
*"make a PDF titled Project Notes about ..."* · *"save that as a text file"*

Files go to `Documents\JARVIS` unless you say desktop or downloads, and he
confirms from the file that is actually on disk — never from having meant to.

---

## Asks first

*"Shut down"* · *"restart"* · *"put the computer to sleep"* ·
*"run this PowerShell command"*

Say "yes" or "cancel". Note that **"go to sleep" means JARVIS**, not the laptop —
suspending the machine requires saying "the computer".

---

## Reading the reactor

| Look | State |
|---|---|
| Slow cyan breathing | Idle, waiting for the wake word |
| Teal, ring reacting to you | Listening |
| Blue, fast counter-rotation | Thinking |
| Violet, sweeping scanner | Running a tool |
| **Gold, pulsing** | Speaking |
| Red | Fault |

The bar under the reactor is the conversation window: while it's counting down,
just keep talking.

---

## A five-minute test pass

1. *"Jarvis"* → *"10 second timer"* — instant, and it goes off
2. Without the wake word: *"what's my battery?"* — should say plugged in
3. *"What's my CPU?"* — CPU only, not everything
4. *"Turn it down"* · *"what's the weather?"* · *"what can you do?"*
5. *"Create a PDF of our conversation on my desktop"* — then look
6. Ask something long, say *"Jarvis"* over him — he stops dead
7. *"No, go to sleep"* — **JARVIS** sleeps, laptop doesn't. Then wake him:
   full screen.

---

## When something is wrong

`logs\jarvis.log`:

- `intent: '...' -> tool(...)` — took the instant path
- `heard: ...` — exactly what he transcribed
- `dropped N frames` — the event loop is blocking; **tell me**
- `woke but heard no speech in 6.0s` — woke, mic gave nothing
- `LISTENER DIED` — capture loop crashed

Run the suites yourself:

```bash
.\.venv\Scripts\python.exe scripts\e2e.py
```
