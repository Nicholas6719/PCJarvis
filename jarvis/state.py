"""JARVIS's operational states. The UI renders directly off these."""
from enum import Enum


class State(str, Enum):
    BOOTING = "booting"      # models warming
    IDLE = "idle"            # listening for the wake word
    LISTENING = "listening"  # you have his attention; capturing speech
    THINKING = "thinking"    # LLM generating
    TOOL = "tool"            # executing a tool call
    SPEAKING = "speaking"    # TTS playing
    ERROR = "error"
