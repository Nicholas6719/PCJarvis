"""JARVIS -- entry point.

Owns the event loop and wires the pieces together:

    Listener (mic -> wake -> VAD -> Whisper)
        -> Brain (Ollama + tools + memory)
            -> Speaker (Kokoro, pipelined) -> Player

Every stage publishes to the bus and the UI renders off it, so the interface can
be closed or replaced without touching any of the above.

The conversation window is the thing that makes it feel alive: after he answers,
you simply keep talking. The wake word is said once per conversation, not once
per sentence.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.audio.listener import Listener  # noqa: E402
from jarvis.audio.mic import Microphone  # noqa: E402
from jarvis.audio.player import Player  # noqa: E402
from jarvis.audio.stt import Transcriber  # noqa: E402
from jarvis.brain import persona  # noqa: E402
from jarvis.brain.llm import Brain  # noqa: E402
from jarvis.brain.memory import Memory  # noqa: E402
from jarvis.bus import BUS  # noqa: E402
from jarvis import (briefing, docs, history, patterns,  # noqa: E402
                    quiet, schedules, standing, vocabulary)
from jarvis.config import CONFIG, DATA_DIR, LOGS_DIR  # noqa: E402
from jarvis import health  # noqa: E402
from jarvis.state import State  # noqa: E402
from jarvis.tools import documents, memory_tools, registry  # noqa: E402
from jarvis.tools import text as text_tools  # noqa: E402
from jarvis.watch import Watcher  # noqa: E402
from jarvis.voice.speaker import Speaker  # noqa: E402
from jarvis.voice.boot_sound import make_boot_sound  # noqa: E402
from jarvis.voice import tones  # noqa: E402
from jarvis.voice.tts import Voice, make_chime  # noqa: E402

log = logging.getLogger("jarvis")

# Strong references to every background task started here.
#
# asyncio keeps only a weak reference to a running task, so a task nobody
# holds can be collected mid-await and simply stop. That is not theory:
# it is exactly why the timer never fired -- the sleeping task was
# collected before it woke. The announcement and window-restore paths
# below await playback for seconds at a time, the widest window of all,
# so every fire-and-forget task goes through _spawn.
_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    """Start a background task and keep it alive until it finishes."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task

YES = {"yes", "yeah", "yep", "confirm", "confirmed", "do it", "go ahead",
       "proceed", "affirmative", "please do", "sure", "correct"}
NO = {"no", "nope", "cancel", "stop", "don't", "do not", "negative",
      "never mind", "nevermind", "forget it"}

# Ending the conversation deliberately, rather than by timeout.
# People do not issue bare commands; they thank you first. "Thank you, go to
# sleep" and "good work, go to sleep" are the natural forms, and an earlier
# version matched neither -- they fell through to the model, where "go to sleep"
# became an attempt to suspend the laptop.
#
# So any amount of courtesy may precede the actual instruction. Each of these
# can also stand alone as a dismissal in its own right ("that's all"), which the
# regex handles by letting the prefix group match zero times.
_COURTESY = (
    r"(?:thank\s*you(?:\s+very\s+much)?|thanks(?:\s+a\s+lot)?|cheers|"
    r"good\s+(?:work|job|stuff)|great\s+(?:work|job)|nice\s+(?:work|job)|"
    r"well\s+done|excellent|perfect|brilliant|awesome|lovely|nice|"
    r"that(?:'s| is)\s+all|that(?:'ll| will)\s+be\s+all|"
    r"ok|okay|alright|all\s+right|right|cool|got\s+it|understood|"
    r"sounds\s+good|no|nope|yes|yeah|yep|actually|just|well|and|then)"
)
_PREFIX = (rf"^(?:(?:jarvis[,\s]+)?{_COURTESY}[,.!\s]+)*"
           r"(?:jarvis[,\s]+)?(?:please\s+)?")

# Stand down, but keep listening. He stays resident and the wake word still
# works -- this is not an exit.
DISMISS = re.compile(
    _PREFIX +
    r"(?:that(?:'s| is) all|that(?:'ll| will) be all|go(?:ing)? to sleep|"
    r"goodbye|good bye|bye|stop listening|"
    r"(?:go\s+)?(?:back|return)\s+to\s+wake\s+mode|wake mode|"
    r"dismissed|nothing else|we(?:'re| are) done|sleep|stand down|"
    r"never ?mind|forget it|that is it|that(?:'s) it)"
    r"[.!\s]*$", re.I)

# Shut JARVIS himself down and close the application. Anchored at the end, so
# "shut down my computer" cannot match here -- that is a different, destructive
# action which goes through confirmation.
SHUTDOWN_SELF = re.compile(
    _PREFIX +
    r"(?:shut\s*down(?:\s+(?:yourself|jarvis))?|shut\s+yourself\s+down|"
    r"exit|quit|power\s+(?:down|off)|terminate|"
    r"close\s+(?:yourself|jarvis|the\s+app))"
    r"[.!\s]*$", re.I)


def setup_logging(level: str = "INFO") -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-22s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOGS_DIR / "jarvis.log", encoding="utf-8"),
        ],
    )
    for noisy in ("httpx", "httpcore", "urllib3", "numba", "faster_whisper",
                  "comtypes", "comtypes.client", "comtypes.client._generate",
                  "comtypes.client._code_cache", "phonemizer", "PIL",
                  "trafilatura", "pywebview", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class Jarvis:
    def __init__(self, cfg=CONFIG):
        self.cfg = cfg
        self.state = State.BOOTING
        self.running = False
        self.awaiting_confirmation = False

        self.memory: Memory | None = None
        self.brain: Brain | None = None
        self.voice: Voice | None = None
        self.mic: Microphone | None = None
        self.player: Player | None = None
        self.speaker: Speaker | None = None
        self.listener: Listener | None = None
        self._chime = None
        self._first_sound_at = None
        # A diagnostic drives real turns through handle(). Without this
        # they are recorded as conversation, and his exported PDF opened
        # with two exchanges that never happened.
        self.record_turns = True
        self._turn: asyncio.Task | None = None
        self._interrupted = False
        self._last_proactive = ""
        self._warm_task: asyncio.Task | None = None
        self._shut_down = False

    # ── state ──────────────────────────────────────────────────────
    async def set_state(self, state: State) -> None:
        if state is self.state:
            return
        self.state = state
        await BUS.emit("state", state=state.value)

    # ── startup ────────────────────────────────────────────────────
    async def boot(self) -> bool:
        BUS.bind_loop(asyncio.get_running_loop())
        await self.set_state(State.BOOTING)
        t0 = time.perf_counter()

        log.info("=" * 58)
        log.info(" J.A.R.V.I.S.  initialising")
        log.info("=" * 58)

        # Before anything heavy loads: reclaim memory a previous run leaked.
        # Ollama orphans its llama-server child on restart or crash, and each
        # orphan keeps holding ~4 GB. They accumulate until a launch fails,
        # which orphans another one.
        # Where quiet hours and snoozes are kept, so both survive a restart.
        quiet.configure(DATA_DIR,
                        self.cfg.get("watch.quiet_expire_hours", 12))
        standing.configure(DATA_DIR)   # things he was asked to watch for
        history.configure(DATA_DIR)    # readings, so trends are answerable
        schedules.configure(DATA_DIR)  # protocols that run on their own
        docs.configure(DATA_DIR)       # his own writing, searchable
        patterns.configure(DATA_DIR)   # habits he has noticed
        vocabulary.configure(DATA_DIR)  # words he has taught the ear

        state = health.startup_check()
        if state.get("orphans_killed"):
            await BUS.emit("boot", step=f"reclaimed "
                                        f"{state['reclaimed_gb']:.1f} GB")
        self.memory_state = state

        log.info("%d tools registered", registry.load_all())

        self.memory = Memory(self.cfg)
        memory_tools.bind(self.memory)
        documents.bind(self.memory)   # so he can export the conversation

        self.brain = Brain(self.cfg, self.memory)
        text_tools.bind(self.brain)   # proofread/rewrite, all on-device
        docs.bind(self.memory._embed)  # the same embedder, not a second one
        ok, message = await self.brain.available()
        if not ok:
            log.error("brain unavailable: %s", message)
            await BUS.emit("error", text=message)
            return False

        await BUS.emit("boot", step="loading models")
        stt, self.voice = await asyncio.gather(
            asyncio.to_thread(
                Transcriber,
                model=self.cfg.get("stt.model", "small.en"),
                compute_type=self.cfg.get("stt.compute_type", "int8"),
                beam_size=self.cfg.get("stt.beam_size", 1),
                cpu_threads=self.cfg.get("stt.cpu_threads", 6),
            ),
            asyncio.to_thread(Voice, self.cfg),
        )

        # The player only needs the voice's sample rate, so it can open here
        # rather than at the end of boot -- which means the power-up plays
        # *during* initialisation instead of after it, and the last of it is
        # still ringing when he says good evening.
        self.player = Player(sample_rate=self.voice.sample_rate,
                             device=self.cfg.get("audio.output_device"))
        self.player.start()
        if self.cfg.get("audio.boot_sound", True):
            asyncio.get_running_loop().run_in_executor(None, self._power_up)

        # Whisper and Kokoro warm in about two seconds and are needed the
        # moment he speaks, so they block. The language model takes ~35s to
        # load onto the GPU and prime its cache -- and nothing deterministic
        # needs it, so it warms in the background. Boot drops from forty
        # seconds to about four, and "set a timer" works immediately;
        # only the first question that genuinely needs the model waits.
        await BUS.emit("boot", step="warming")
        await asyncio.gather(
            asyncio.to_thread(stt.warm),
            asyncio.to_thread(self.voice.warm),
        )
        self._warm_task = asyncio.create_task(self._warm_brain())

        self.mic = Microphone(
            sample_rate=self.cfg.get("audio.sample_rate", 16000),
            block_size=self.cfg.get("audio.block_size", 1280),
            device=self.cfg.get("audio.input_device"),
            preroll_ms=self.cfg.get("vad.preroll_ms", 2000),
        )
        self.speaker = Speaker(self.voice, self.player)
        self.listener = Listener(self.cfg, self.mic, stt)
        self._chime = make_chime(self.voice.sample_rate)

        # The part of him that speaks first. It only observes here;
        # nothing is said until run() starts its loop.
        self.watcher = Watcher(self.cfg, state_getter=lambda: self.state,
                               brain=self.brain)

        self.mic.start()

        log.info("ready in %.1fs (voice=%s, model=%s, wake=%.2f)",
                 time.perf_counter() - t0, self.voice.voice,
                 self.cfg.get("llm.model"), self.cfg.get("wake.threshold"))
        await self.set_state(State.IDLE)
        return True

    async def _warm_brain(self) -> None:
        """Load the model and prime its cache without holding up startup."""
        t0 = time.perf_counter()
        try:
            await self.brain.warm()
            log.info("brain warm after %.1fs (deterministic commands were "
                     "available throughout)", time.perf_counter() - t0)
            await BUS.emit("brain.ready")
        except Exception:
            log.exception("background warm failed")

    # ── speaking ───────────────────────────────────────────────────
    async def speak(self, text: str, wait: bool = True,
                    conversational: bool = True) -> bool:
        """Queue speech. Returns False if he was interrupted.

        conversational=False is for things he did not ask for -- the startup
        greeting, mainly. Those must not open a conversation window, or the
        interface announces "no wake word needed" when the wake word is in fact
        still required.
        """
        if not text.strip() or not self.speaker or not self.listener:
            return True
        await self.set_state(State.SPEAKING)
        self.listener.begin_speaking()
        await BUS.emit("speaking", text=text)
        self.speaker.say(text)
        if not wait:
            return True
        finished = await self.speaker.wait_until_done()
        await self._after_speaking(conversational=conversational)
        return finished

    async def _after_speaking(self, conversational: bool = True) -> None:
        """Settle, flush the mic, and reopen the conversation window."""
        if not self.listener:
            return
        guard = self.cfg.get("vad.post_speech_guard_ms", 300) / 1000
        await asyncio.sleep(guard)
        self.listener.end_speaking()

        if not conversational:
            self.listener.end_conversation()
            await BUS.emit("conversation.ended")
            await self.set_state(State.IDLE)
            return

        await BUS.emit("conversation.open",
                       seconds=self.cfg.get("conversation.window_s", 15))
        await self.set_state(State.LISTENING if self.listener.in_conversation
                             else State.IDLE)

    def _power_up(self) -> None:
        """The arc reactor coming up to speed, on a worker thread.

        Synthesising it costs a few hundred milliseconds of numpy, which is
        not much but is pure dead air if it happens on the event loop while
        the models are trying to load.
        """
        try:
            audio = make_boot_sound(self.voice.sample_rate)
            self.player.play(audio, self.voice.sample_rate)
            log.info("power-up: %.1fs of arc reactor",
                     len(audio) / self.voice.sample_rate)
        except Exception:
            log.debug("boot sound failed; carrying on in silence",
                      exc_info=True)

    def tone(self, name: str) -> None:
        """A short sound from the palette. Silent if switched off."""
        if not self.cfg.get("audio.tones", True) or not self.speaker:
            return
        try:
            audio = tones.make(name, self.voice.sample_rate)
            if audio is not None:
                self.speaker.play_audio(audio, self.voice.sample_rate)
        except Exception:
            log.debug("tone %s failed", name, exc_info=True)

    def chime(self) -> None:
        if self.cfg.get("wake.chime", True) and self.speaker:
            self.speaker.play_audio(self._chime, self.voice.sample_rate)

    # ── the conversation ───────────────────────────────────────────
    async def handle(self, text: str) -> None:
        """One user utterance, from transcript to spoken reply."""
        assert self.brain is not None and self.listener is not None

        # Where the time actually goes, per turn. "It feels laggy" is not
        # something anyone can act on, and guessing at it once already cost
        # a round of work on the wrong thing. The number that matters is the
        # first one: how long he waits before hearing anything at all.
        _turn_t0 = time.perf_counter()
        self._first_sound_at = None

        # Only the long-session observation uses this: it will not
        # remark on how long he has been at the desk if he walked away
        # from it half an hour ago.
        if getattr(self, "watcher", None):
            self.watcher.note_activity()

        self._interrupted = False
        # Hold the window open for the whole turn. Without this a slow reply
        # expires it mid-answer and he drops to wake mode while still speaking.
        self.listener.suspend_conversation()
        if self.memory:
            if self.record_turns:
                self.memory.log_turn("user", text)

        # "Shut down" -- close the application. Checked before dismissal, since
        # the two share courtesy prefixes and this is the stronger instruction.
        # Anything about the *computer* falls through to the model and its
        # confirmation gate; this only ever ends JARVIS.
        if SHUTDOWN_SELF.match(text.strip()):
            log.info("shutdown requested by voice")
            await self.speak(persona.pick(persona.FAREWELL_PHRASES, self.cfg))
            await self.quit_now()
            return

        # "That's all" -- stand down but stay resident.
        if DISMISS.match(text.strip()):
            await self.speak(persona.pick(persona.DISMISS_PHRASES, self.cfg))
            await self.sleep_now()
            return

        # A pending confirmation swallows the next yes/no.
        if self.awaiting_confirmation:
            lowered = text.lower().strip().rstrip(".!?")
            approved = any(w in lowered for w in YES)
            declined = any(w in lowered for w in NO)
            if approved or declined:
                self.awaiting_confirmation = False
                await self.set_state(State.TOOL)
                result = await self.brain.resolve_confirmation(approved)
                await self.speak(result)
                return
            self.awaiting_confirmation = False  # unrelated -- a new request

        await self.set_state(State.THINKING)
        spoke = False

        try:
            async for event in self.brain.respond(text):
                if self._interrupted:
                    break

                if event.type == "sentence":
                    if self._first_sound_at is None:
                        self._first_sound_at = time.perf_counter()
                    spoke = True
                    # Queue and keep going: synthesis runs a sentence ahead of
                    # playback, so the reply comes out as one continuous piece.
                    if self.listener:
                        self.listener.begin_speaking()
                    await self.set_state(State.SPEAKING)
                    await BUS.emit("speaking", text=event.text)
                    self.speaker.say(event.text)

                elif event.type == "tool_start":
                    await self.set_state(State.TOOL)
                    await BUS.emit("tool", name=event.name,
                                   arguments=(event.data or {}).get("arguments"))

                elif event.type == "tool_end":
                    await BUS.emit("tool_result", name=event.name,
                                   result=event.text)

                elif event.type == "confirm":
                    self.awaiting_confirmation = True
                    spoke = True
                    await self.speak(event.text, wait=False)
                    await BUS.emit("confirm", name=event.name, text=event.text)

                elif event.type == "done":
                    if event.text and self.memory:
                        if self.record_turns:
                            self.memory.log_turn("assistant", event.text)
                    await BUS.emit("reply", text=event.text)

                elif event.type == "error":
                    log.error("brain error: %s", event.text)
                    self.speaker.say(persona.pick(persona.ERROR_PHRASES, self.cfg))
                    spoke = True

        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("failed while handling utterance")
            self.speaker.say(persona.pick(persona.ERROR_PHRASES, self.cfg))
            spoke = True

        if self._interrupted:
            return
        if not spoke:
            self.speaker.say(persona.pick(persona.UNCLEAR_PHRASES, self.cfg))

        await self.speaker.wait_until_done()

        total = time.perf_counter() - _turn_t0
        if self._first_sound_at is not None:
            log.info("turn: %.2fs to first word, %.2fs total  (%r)",
                     self._first_sound_at - _turn_t0, total, text[:44])
        else:
            log.info("turn: %.2fs, nothing spoken  (%r)", total, text[:44])

        self.listener.resume_conversation()
        await self._after_speaking()

    # ── wake / sleep ───────────────────────────────────────────────
    def _on_wake(self) -> None:
        self.chime()
        # Whatever he was -- idle, asleep -- he is awake now.
        _spawn(BUS.emit("conversation.open",
                        seconds=self.cfg.get("conversation.window_s", 15)))
        if self.listener:
            self.listener.extend_conversation()
        _spawn(self.set_state(State.LISTENING))
        _spawn(BUS.emit("window.restore"))

    def _on_barge_in(self) -> None:
        """He was cut off. Drop everything and listen."""
        self._interrupted = True
        if self.speaker:
            self.speaker.stop()
        if self._turn and not self._turn.done():
            self._turn.cancel()
        if self.listener:
            self.listener.end_speaking()
            self.listener.extend_conversation()
        self.chime()
        _spawn(self.set_state(State.LISTENING))

    # ── speaking unprompted ────────────────────────────────────────
    async def _say_proactively(self, text: str) -> None:
        """A timer elapsed, or something else wants the floor.

        Rules, in order: never while muted, never on top of a reply in
        progress, and never the same announcement twice. Waiting is preferable
        to interrupting -- an announcement that lands mid-sentence is worse
        than one that lands ten seconds late.
        """
        if not text or not self.listener or self.listener.muted:
            return
        if text == self._last_proactive:
            return

        for _ in range(120):          # up to ~30s of politeness
            busy = (self.state in (State.THINKING, State.TOOL, State.SPEAKING)
                    or (self.speaker and self.speaker.is_speaking))
            if not busy:
                break
            await asyncio.sleep(0.25)
        else:
            log.info("dropped a proactive announcement: still busy")
            return

        self._last_proactive = text
        log.info("speaking unprompted: %s", text)
        # A sound before speech nobody asked for. It marks the difference
        # between an answer and an interruption, which is otherwise only
        # apparent once he is already talking.
        self.tone("attention")
        await asyncio.sleep(0.32)
        await BUS.emit("proactive.spoken", text=text)
        await self.speak(text)

    async def _on_conversation_ended(self) -> None:
        if self.state in (State.LISTENING, State.IDLE):
            await self.set_state(State.IDLE)

    async def sleep_now(self) -> None:
        """Return to wake mode and get out of the way. Still listening."""
        if self.listener:
            self.listener.end_conversation()
        await BUS.emit("conversation.ended")
        # SLEEPING, not IDLE: the interface shows a distinctly dormant
        # reactor so a glance tells him whether he was dismissed or simply
        # timed out.
        await self.set_state(State.SLEEPING)
        if self.cfg.get("ui.minimize_on_sleep", True):
            await BUS.emit("window.minimize")

    async def quit_now(self) -> None:
        """Close JARVIS entirely.

        Distinct from sleep: nothing is left listening, the window closes, and
        the process exits. The farewell has already been spoken by the time we
        get here, so playback is allowed to drain before anything is torn down.
        """
        log.info("shutting down at the user's request")
        await self.set_state(State.STOPPING)
        if self.speaker:
            await self.speaker.wait_until_done(timeout=8)
        if self.listener:
            self.listener.end_conversation()
        self.running = False
        await BUS.emit("app.quit")

    # ── main loop ──────────────────────────────────────────────────
    async def run(self, greet: bool = True) -> None:
        self.running = True
        assert self.listener is not None

        BUS.on("ui.trigger", lambda _: self.listener.trigger())
        BUS.on("ui.interrupt", lambda _: self._on_barge_in())
        BUS.on("wake.detected", lambda _: self._on_wake())
        BUS.on("barge_in", lambda _: self._on_barge_in())
        BUS.on("proactive", lambda ev: _spawn(
            self._say_proactively(ev.get("text", ""))))
        # When the window closes the interface must stop saying "listening".
        BUS.on("conversation.ended", lambda _: _spawn(
            self._on_conversation_ended()))


        if self.cfg.get("watch.enabled", True):
            _spawn(self.watcher.run())

        listen_task = asyncio.create_task(self.listener.run())
        listen_task.add_done_callback(self._listener_died)

        # Headless has no window to close, so the quit signal has to stop
        # the loop itself.
        quit_signal = asyncio.Event()
        BUS.on("app.quit", lambda _: quit_signal.set())

        # If he cannot be heard, say so before anything else. A muted
        # microphone looks exactly like a broken wake word from where he is
        # sitting -- the device opens, reports itself fine, and returns
        # silence -- and he has no way to tell those apart.
        if self.memory_state.get("mic_muted"):
            await self.speak("Your microphone is muted, sir. I will not "
                             "hear you until it is unmuted.")

        if greet:
            # Not conversational: he has not asked for anything yet, so the
            # window stays shut and the wake word is still required.
            hello = persona.pick(persona.GREETINGS, self.cfg)

            # What happened while he was away, if anything did. Usually this
            # is empty and he simply says good evening, which is correct --
            # a status report after a ten minute absence is an alarm system,
            # not a butler.
            report = briefing.missed()
            await self.speak(f"{hello} {report}".strip(),
                             conversational=False)

        try:
            async for text in self.listener.utterances():
                if not self.running or quit_signal.is_set():
                    break
                self._turn = asyncio.create_task(self.handle(text))
                try:
                    await self._turn
                except asyncio.CancelledError:
                    log.info("turn cancelled by interruption")
        except asyncio.CancelledError:
            pass
        finally:
            listen_task.cancel()

    @staticmethod
    def _listener_died(task: asyncio.Task) -> None:
        """A crash in the capture loop is silent otherwise, and looks exactly
        like a broken microphone."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            log.error("LISTENER DIED -- he can no longer hear anything",
                      exc_info=exc)

    async def shutdown(self) -> None:
        """Tear everything down, including the model.

        Runs on every exit path -- voice shutdown, the close button,
        Alt+F4 -- because the expensive thing is not JARVIS, it is the
        eight gigabytes Ollama keeps hold of after we are gone.
        """
        if self._shut_down:
            return                      # exit paths can overlap
        self._shut_down = True
        self.running = False
        log.info("shutting down")

        if getattr(self, 'watcher', None):
            self.watcher.stop()
        history.close()
        docs.close()

        if self.speaker:
            self.speaker.shutdown()
        if self.mic:
            self.mic.stop()
        if self.player:
            self.player.stop()
        if self.memory:
            self.memory.close()

        if self.cfg.get("llm.stop_ollama_on_exit", True):
            try:
                stopped, freed = await asyncio.to_thread(health.stop_ollama)
                if stopped:
                    log.info("released %.1f GB by stopping Ollama", freed)
            except Exception:
                log.debug("could not stop Ollama", exc_info=True)


# ── console rendering, for --no-ui ─────────────────────────────────
def attach_console() -> None:
    def show(ev: dict) -> None:
        kind = ev.get("event")
        if kind == "listen.transcript":
            print(f"\n  YOU     {ev['text']}")
        elif kind == "speaking":
            print(f"  JARVIS  {ev['text']}")
        elif kind == "tool":
            print(f"  ...     {ev['name']}({ev.get('arguments') or ''})")
        elif kind == "wake.detected":
            print("\n  [awake]")
        elif kind == "conversation.open":
            print(f"  [listening -- {ev.get('seconds')}s, no wake word needed]")
        elif kind == "conversation.ended":
            print("  [back to wake mode]")
        elif kind == "barge_in":
            print("  [interrupted]")
    BUS.on("*", show)


async def amain(args) -> int:
    setup_logging(CONFIG.get("system.log_level", "INFO"))
    app = Jarvis(CONFIG)

    if args.no_ui:
        attach_console()

    if not await app.boot():
        print("\nJARVIS could not start. Check that Ollama is running:\n"
              "    ollama serve\n")
        return 1

    if args.say:
        await app.speak(args.say)
        await app.shutdown()
        return 0

    if args.ask:
        await app.handle(args.ask)
        await app.shutdown()
        return 0

    try:
        await app.run(greet=not args.quiet)
    except KeyboardInterrupt:
        pass
    finally:
        await app.shutdown()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="JARVIS")
    ap.add_argument("--no-ui", action="store_true", help="run in the terminal")
    ap.add_argument("--quiet", action="store_true", help="skip the greeting")
    ap.add_argument("--say", type=str, help="speak one line and exit")
    ap.add_argument("--ask", type=str, help="ask one question and exit")
    args = ap.parse_args()

    if args.no_ui or args.say or args.ask:
        try:
            return asyncio.run(amain(args))
        except KeyboardInterrupt:
            return 0

    from jarvis.ui.window import run_windowed
    return run_windowed(args)


if __name__ == "__main__":
    raise SystemExit(main())
