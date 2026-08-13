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
from jarvis.config import CONFIG, LOGS_DIR  # noqa: E402
from jarvis.state import State  # noqa: E402
from jarvis.tools import documents, memory_tools, registry  # noqa: E402
from jarvis.voice.speaker import Speaker  # noqa: E402
from jarvis.voice.tts import Voice, make_chime  # noqa: E402

log = logging.getLogger("jarvis")

YES = {"yes", "yeah", "yep", "confirm", "confirmed", "do it", "go ahead",
       "proceed", "affirmative", "please do", "sure", "correct"}
NO = {"no", "nope", "cancel", "stop", "don't", "do not", "negative",
      "never mind", "nevermind", "forget it"}

# Ending the conversation deliberately, rather than by timeout.
# Leading filler is common and was fatal: "no, go to sleep" failed to match,
# fell through to the model, and became an attempt to suspend the laptop.
# "go to sleep" addressed to JARVIS always means JARVIS -- suspending the
# machine requires saying so explicitly.
DISMISS = re.compile(
    r"^(?:(?:no|nope|yes|yeah|ok|okay|actually|just|well|and|then)[,\s]+)*"
    r"(?:jarvis[,\s]+)?(?:please\s+)?"
    r"(?:that(?:'s| is) all|that will be all|go to sleep|goodbye|good bye|"
    r"bye|thanks?,? that(?:'s| is) it|stop listening|return to wake mode|"
    r"dismissed|nothing else|we(?:'re| are) done|sleep|stand down|"
    r"never ?mind|forget it)"
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
        self._turn: asyncio.Task | None = None
        self._interrupted = False
        self._last_proactive = ""
        self._warm_task: asyncio.Task | None = None

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
        log.info("%d tools registered", registry.load_all())

        self.memory = Memory(self.cfg)
        memory_tools.bind(self.memory)
        documents.bind(self.memory)   # so he can export the conversation

        self.brain = Brain(self.cfg, self.memory)
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
        self.player = Player(sample_rate=self.voice.sample_rate,
                             device=self.cfg.get("audio.output_device"))
        self.speaker = Speaker(self.voice, self.player)
        self.listener = Listener(self.cfg, self.mic, stt)
        self._chime = make_chime(self.voice.sample_rate)

        self.mic.start()
        self.player.start()

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

    def chime(self) -> None:
        if self.cfg.get("wake.chime", True) and self.speaker:
            self.speaker.play_audio(self._chime, self.voice.sample_rate)

    # ── the conversation ───────────────────────────────────────────
    async def handle(self, text: str) -> None:
        """One user utterance, from transcript to spoken reply."""
        assert self.brain is not None and self.listener is not None

        self._interrupted = False
        # Hold the window open for the whole turn. Without this a slow reply
        # expires it mid-answer and he drops to wake mode while still speaking.
        self.listener.suspend_conversation()
        if self.memory:
            self.memory.log_turn("user", text)

        # "That's all" -- deliberate dismissal.
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
        self.listener.resume_conversation()
        await self._after_speaking()

    # ── wake / sleep ───────────────────────────────────────────────
    def _on_wake(self) -> None:
        self.chime()
        if self.listener:
            self.listener.extend_conversation()
        asyncio.create_task(self.set_state(State.LISTENING))
        asyncio.create_task(BUS.emit("window.restore"))

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
        asyncio.create_task(self.set_state(State.LISTENING))

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
        await BUS.emit("proactive.spoken", text=text)
        await self.speak(text)

    async def _on_conversation_ended(self) -> None:
        if self.state in (State.LISTENING, State.IDLE):
            await self.set_state(State.IDLE)

    async def sleep_now(self) -> None:
        """Return to wake mode and get out of the way."""
        if self.listener:
            self.listener.end_conversation()
        await self.set_state(State.IDLE)
        await BUS.emit("conversation.ended")
        if self.cfg.get("ui.minimize_on_sleep", True):
            await BUS.emit("window.minimize")

    # ── main loop ──────────────────────────────────────────────────
    async def run(self, greet: bool = True) -> None:
        self.running = True
        assert self.listener is not None

        BUS.on("ui.trigger", lambda _: self.listener.trigger())
        BUS.on("ui.interrupt", lambda _: self._on_barge_in())
        BUS.on("wake.detected", lambda _: self._on_wake())
        BUS.on("barge_in", lambda _: self._on_barge_in())
        BUS.on("proactive", lambda ev: asyncio.create_task(
            self._say_proactively(ev.get("text", ""))))
        # When the window closes the interface must stop saying "listening".
        BUS.on("conversation.ended", lambda _: asyncio.create_task(
            self._on_conversation_ended()))

        listen_task = asyncio.create_task(self.listener.run())
        listen_task.add_done_callback(self._listener_died)

        if greet:
            # Not conversational: he has not asked for anything yet, so the
            # window stays shut and the wake word is still required.
            await self.speak(persona.pick(persona.GREETINGS, self.cfg),
                             conversational=False)

        try:
            async for text in self.listener.utterances():
                if not self.running:
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
        self.running = False
        log.info("shutting down")
        if self.speaker:
            self.speaker.shutdown()
        if self.mic:
            self.mic.stop()
        if self.player:
            self.player.stop()
        if self.memory:
            self.memory.close()


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
