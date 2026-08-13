"""JARVIS -- entry point.

Owns the event loop and wires the pieces together:

    Listener (mic -> wake -> VAD -> Whisper)
        -> Brain (Ollama + tools + memory)
            -> Voice (Kokoro) -> Player

Every stage publishes to the bus, and the UI renders whatever it sees there, so
the interface can be closed or replaced without touching any of the above.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.audio.listener import Listener  # noqa: E402
from jarvis.audio.mic import Microphone  # noqa: E402
from jarvis.audio.player import Player  # noqa: E402
from jarvis.audio.stt import Transcriber  # noqa: E402
from jarvis.brain.llm import Brain  # noqa: E402
from jarvis.brain.memory import Memory  # noqa: E402
from jarvis.brain import persona  # noqa: E402
from jarvis.bus import BUS  # noqa: E402
from jarvis.config import CONFIG, LOGS_DIR  # noqa: E402
from jarvis.state import State  # noqa: E402
from jarvis.tools import memory_tools, registry  # noqa: E402
from jarvis.voice.tts import Voice, make_chime  # noqa: E402

log = logging.getLogger("jarvis")

# Words that answer a pending confirmation.
YES = {"yes", "yeah", "yep", "confirm", "confirmed", "do it", "go ahead",
       "proceed", "affirmative", "please do", "sure", "correct"}
NO = {"no", "nope", "cancel", "stop", "don't", "do not", "negative",
      "never mind", "nevermind", "forget it"}


def setup_logging(level: str = "INFO") -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
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
        self.listener: Listener | None = None
        self._chime = None

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

        n_tools = registry.load_all()
        log.info("%d tools registered", n_tools)

        self.memory = Memory(self.cfg)
        memory_tools.bind(self.memory)

        self.brain = Brain(self.cfg, self.memory)
        ok, message = await self.brain.available()
        if not ok:
            log.error("brain unavailable: %s", message)
            await BUS.emit("error", text=message)
            return False

        # Load the heavy models concurrently -- Whisper and Kokoro are both
        # several hundred megabytes and neither depends on the other.
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

        await BUS.emit("boot", step="warming")
        await asyncio.gather(
            asyncio.to_thread(stt.warm),
            asyncio.to_thread(self.voice.warm),
            self.brain.warm(),
        )

        self.mic = Microphone(
            sample_rate=self.cfg.get("audio.sample_rate", 16000),
            block_size=self.cfg.get("audio.block_size", 1280),
            device=self.cfg.get("audio.input_device"),
            preroll_ms=self.cfg.get("vad.preroll_ms", 300),
        )
        self.player = Player(
            sample_rate=self.voice.sample_rate,
            device=self.cfg.get("audio.output_device"),
        )
        self.listener = Listener(self.cfg, self.mic, stt)
        self._chime = make_chime(self.voice.sample_rate)

        self.mic.start()
        self.player.start()

        log.info("ready in %.1fs (voice=%s, model=%s)",
                 time.perf_counter() - t0, self.voice.voice,
                 self.cfg.get("llm.model"))
        await self.set_state(State.IDLE)
        return True

    # ── speaking ───────────────────────────────────────────────────
    async def speak(self, text: str) -> None:
        """Synthesize and play. The mic is paused so he never hears himself."""
        if not text.strip() or not self.voice or not self.player:
            return
        await self.set_state(State.SPEAKING)
        if self.listener:
            self.listener.pause()
        try:
            audio, sr = await asyncio.to_thread(self.voice.say, text)
            await BUS.emit("speaking", text=text)
            await self.player.play_and_wait(audio, sr)
        except Exception:
            log.exception("speech failed")
        finally:
            if self.listener:
                self.listener.resume()

    def chime(self) -> None:
        if self.cfg.get("wake.chime", True) and self.player is not None:
            self.player.play(self._chime, self.voice.sample_rate)

    # ── the conversation ───────────────────────────────────────────
    async def handle(self, text: str) -> None:
        """One user utterance, from transcript to spoken reply."""
        assert self.brain is not None

        if self.memory:
            self.memory.log_turn("user", text)

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
                await self.set_state(State.IDLE)
                return
            self.awaiting_confirmation = False  # unrelated -- treat as a new ask

        await self.set_state(State.THINKING)
        spoken_any = False

        try:
            async for event in self.brain.respond(text):
                if event.type == "sentence":
                    spoken_any = True
                    await self.speak(event.text)
                    await self.set_state(State.THINKING)

                elif event.type == "tool_start":
                    await self.set_state(State.TOOL)
                    await BUS.emit("tool", name=event.name,
                                   arguments=(event.data or {}).get("arguments"))

                elif event.type == "tool_end":
                    await BUS.emit("tool_result", name=event.name,
                                   result=event.text)

                elif event.type == "confirm":
                    self.awaiting_confirmation = True
                    await self.speak(event.text)
                    await BUS.emit("confirm", name=event.name, text=event.text)

                elif event.type == "done":
                    if event.text and self.memory:
                        self.memory.log_turn("assistant", event.text)
                    await BUS.emit("reply", text=event.text)

                elif event.type == "error":
                    log.error("brain error: %s", event.text)
                    await self.speak(persona.pick(persona.ERROR_PHRASES, self.cfg))
                    spoken_any = True
        except Exception:
            log.exception("failed while handling utterance")
            await self.speak(persona.pick(persona.ERROR_PHRASES, self.cfg))
            spoken_any = True

        if not spoken_any:
            await self.speak(persona.pick(persona.UNCLEAR_PHRASES, self.cfg))

        await self.set_state(State.IDLE if not self.awaiting_confirmation
                             else State.IDLE)

    # ── main loop ──────────────────────────────────────────────────
    async def run(self, greet: bool = True) -> None:
        self.running = True
        assert self.listener is not None

        BUS.on("ui.trigger", lambda _: self.listener.trigger())
        BUS.on("ui.interrupt", lambda _: self.player.interrupt())
        BUS.on("wake.detected", lambda _: self._on_wake())

        listen_task = asyncio.create_task(self.listener.run())
        if greet:
            await self.speak(persona.pick(persona.GREETINGS, self.cfg))

        try:
            async for text in self.listener.utterances():
                if not self.running:
                    break
                await self.handle(text)
        except asyncio.CancelledError:
            pass
        finally:
            listen_task.cancel()

    def _on_wake(self) -> None:
        # Barge-in first, then chime. The other order queues the chime and then
        # immediately interrupts it, so he gets silence and no idea he was heard.
        if self.player and self.player.is_playing:
            self.player.interrupt()
        self.chime()
        asyncio.create_task(self.set_state(State.LISTENING))

    async def shutdown(self) -> None:
        self.running = False
        log.info("shutting down")
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
        elif kind == "state":
            pass
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
    ap.add_argument("--no-ui", action="store_true",
                    help="run headless in the terminal")
    ap.add_argument("--quiet", action="store_true", help="skip the greeting")
    ap.add_argument("--say", type=str, help="speak one line and exit")
    ap.add_argument("--ask", type=str, help="ask one question and exit")
    args = ap.parse_args()

    if args.no_ui or args.say or args.ask:
        try:
            return asyncio.run(amain(args))
        except KeyboardInterrupt:
            return 0

    # Windowed mode: the UI owns the main thread, the loop runs beneath it.
    from jarvis.ui.window import run_windowed
    return run_windowed(args)


if __name__ == "__main__":
    raise SystemExit(main())
