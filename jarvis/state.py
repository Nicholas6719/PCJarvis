"""JARVIS's operational states. The UI renders directly off these."""
from enum import Enum


class State(str, Enum):
    BOOTING = "booting"      # models warming
    IDLE = "idle"            # listening for the wake word
    LISTENING = "listening"  # you have his attention; capturing speech
    THINKING = "thinking"    # LLM generating
    TOOL = "tool"            # executing a tool call
    SPEAKING = "speaking"    # TTS playing
    # Dismissed on purpose: minimised, still listening for the wake word.
    # Distinct from IDLE, which is awake-and-waiting with the window up --
    # the interface should not look the same for both.
    SLEEPING = "sleeping"
    STOPPING = "stopping"    # shutting down; the window is about to close
    ERROR = "error"
