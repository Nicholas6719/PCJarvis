# Plan: from "the fundamentals are there" to "it truly works"

Derived from the macOS handoff reference, plus four decisions:
full screen that stays put · Spotify linked · wake-word-gated barge-in ·
screen awareness deferred.

The music failure is the whole thesis in miniature. "Play music" produced a
claimed action, a follow-up question, and then a drop back to wake mode before
the question could be answered. Three separate defects: no tool that actually
plays music, a model willing to claim an action it did not take, and no
conversation window. Phase 1 and 2 exist to kill all three.

---

## Correction carried into this plan

When the wake word stopped working I diagnosed the chime being heard as speech,
and added a 450ms settle window that discards the pre-wake buffer. That
diagnosis was wrong — silero scores pure tones at ~0.0005 and never fired on the
chime. The real fault was the VAD receiving the wrong input shape.

The settle window is therefore unnecessary, and it actively breaks the doc's
single-breath capture: "Hey Jarvis, what time is it" currently loses everything
after the wake word. Phase 1 deletes it and restores the rolling buffer.

---

## Phase 1 — The conversation loop

This is the phase that changes how it feels. Everything here serves continuity.

**1.1 Conversation window.** A proper state machine: `WAKE`, `CONVERSATION`
(sub-states listening / thinking / speaking), `MUTED`, `SLEEP`. A 15s timer
resets on every user turn and again when he finishes speaking, and is paused
while he speaks so a long answer never burns the window. On expiry, back to
`WAKE`. After this, the wake word is said once per conversation, not once per
sentence.

**1.2 Restore single-breath capture.** Delete the settle window. Keep a rolling
~2s pre-wake buffer and replay it into the recorder when the wake word fires, so
the command spoken in the same breath as the wake word survives. Post-speech
guard of ~0.3s plus a mic flush before listening resumes.

**1.3 Both wake words.** Train a custom `jarvis` model with openWakeWord's
synthetic-data pipeline and run it alongside `hey_jarvis`; the library supports
multiple models on one stream. Independent thresholds — `hey_jarvis` at 0.75 per
the Mac build, bare `jarvis` higher, because a two-syllable real word will
false-trigger more. If the trained model is poor, fall back to `hey_jarvis` at a
lower threshold and say so rather than shipping something that fires at the
television.

**1.4 Barge-in, wake-word gated.** Stop muting the mic during playback. Keep the
wake-word model running while he speaks; on a fire (or a typed message),
hard-stop the player, flush queued sentences, cancel the in-flight reply, and
treat the interruption as the next turn. A half-spoken reply must not be saved
to history as though it completed. Requires making `Brain.respond` cancellable.

**1.5 Gapless playback.** One continuous output stream for a whole reply rather
than a separate `play()` per sentence, so sentences join without a seam, with a
hard-stop path that drops the buffer instantly for 1.4.

**1.6 Sleep and dismiss.** "Go to sleep", "that's all", "goodbye", "stop
listening" → `WAKE` and minimise. Wake word restores full screen and foreground.

**1.7 Always full screen.** Launch full screen every time, unconditionally.
Minimise only on explicit dismissal — never on a silence timeout.

**1.8 Fast path.** Eight words or fewer with no command keyword → skip tool
schemas and memory entirely, light LLM call with the last three turns. Should
land conversational chit-chat near one second.

**1.9 Persona tightening.** "Sir" or "Nicholas" at most once per reply, at a
sentence end, never both, never twice — most replies use neither. He currently
overuses it.

---

## Phase 2 — Make actions real

**2.1 Spotify.** *Superseded.* This originally specified an
authorization-code flow with a client ID and secret from a free developer
app. Nicholas ruled that out directly: "I want it to use my physical
player... I don't want to use any ID or anything like that."

What was built instead is keyless. A track or playlist name is resolved to
a `spotify:` URI through a web search, and the URI is handed to the
desktop app, which plays it immediately -- no account link, no token, no
secret in the repo. SMTC remains the fallback for every other player.

The cost of going keyless is that the tool cannot read Spotify's library
directly, so resolution depends on the search result being right. That is
why playback is verified against the live media session rather than
assumed: an earlier version announced "playing the lofi beats playlist"
while ZZ Top was actually playing.

**2.2 Deterministic coverage.** Move the common imperatives out of the model's
hands entirely — play/stop/next, volume, apps, folders, browser, timers. The
model should be the last resort, not the first.

**2.3 Capability honesty.** Audit what he is asked to do against what a tool can
actually do, and make the gaps explicit refusals rather than confident fiction.
A plain "I can't start a specific track without your Spotify linked" is worth
more than a pleasant lie.

---

## Phase 3 — Feature parity

Mechanical, low-risk, done in one sweep. All five have landed:

- **Done.** Windows app + folder alias map; open standard folders. Folder
  names resolve through `folders.py` rather than `Path.home()`, because
  OneDrive redirects Desktop, Documents and Pictures on this machine and
  the naive path is a real folder that never appears on screen.
- **Done.** Browser control: open a site, search, new tab, close tab, and
  "what page am I on". Read from the window title -- no extension, no
  debugging port. Closing a tab refuses unless a browser is genuinely
  focused, since ctrl+w elsewhere closes a document.
- **Done.** Clipboard augmentation: proofread / rewrite / summarise /
  translate. Runs on the local model, so nothing copied leaves the
  machine. The result goes back on the clipboard; a summary is spoken
  instead, because one you must paste to read is no use.
- **Done.** Timers, spoken naturally. Note the failure that took three
  attempts: the task was collected mid-sleep because asyncio holds only a
  weak reference to it. `scripts/audit.py` now checks for that shape.
- **Done.** Navigation: "directions to X" opens a route.

---

## Phase 4 — Proactive, and calendar

Proactive speech needs the rules from the doc: never talk over an active reply,
never fire while muted, dedupe so nothing repeats, and — given 1.4 — a proactive
announcement must itself be interruptible. Timers are the natural first consumer.

Calendar and reminders need a provider decision that has not been made yet:
Windows/Outlook via Graph, or Google Calendar. Both mean an OAuth link. Deferred
until Phases 1-3 are solid.

---

## Deferred, deliberately

**Screen awareness.** Needs a vision model, and the machine already sits at
94-98% RAM with qwen 7B, Whisper, Kokoro and the embedder resident. There is no
room for a second model without either swapping on demand (~20s per request) or
dropping the brain to 3B and losing the persona quality. Revisit when the rest
is solid.

**Acoustic echo cancellation.** Real barge-in by talking over him. Option B
first; AEC as its own piece of work.

---

## Feel constants adopted from the Mac build

| | |
|---|---|
| Wake threshold | 0.75 (`hey_jarvis`), higher for bare `jarvis` |
| Conversation window | 15s, reset per turn, paused while speaking |
| End-of-speech silence | ~700ms, ~750ms after 2.5s of speech |
| Hard max recording | 15s |
| Pre-wake rolling buffer | ~2s |
| Post-speech guard | ~0.3s + mic flush |
| TTS output | 24 kHz mono float |
| Fast path | ≤8 words, no command keyword, last 3 turns |

One deliberate deviation: mic frames stay at 80ms rather than 30ms, because
openWakeWord consumes exactly 1280 samples at 16 kHz. The VAD subdivides into
512-sample windows internally, so endpointing resolution is unaffected.

---

## Order of work

Phase 1 first and as one piece — the state machine, buffer, barge-in and
playback changes all touch the same loop, and splitting them means integrating
twice. Phase 2 immediately after, since it is what makes "play music" honest.
Phase 3 is a sweep that can land incrementally. Phase 4 last.

Acceptance for Phase 1 is behavioural, not unit: ask a question, get an answer,
ask a follow-up without the wake word, interrupt him mid-sentence with the wake
word, say "that's all", watch it minimise, say "Jarvis" and watch it come back
full screen.
