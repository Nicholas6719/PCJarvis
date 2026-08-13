"""Wake word: "Hey JARVIS".

openWakeWord ships a pretrained hey_jarvis model, which is a rather fortunate
coincidence for this project. Runs on ONNX/CPU at a couple percent of one core.
"""
from __future__ import annotations

import logging
import time

import numpy as np

from ..config import MODELS_DIR

log = logging.getLogger("jarvis.wake")


class WakeWord:
    def __init__(
        self,
        model_name: str = "hey_jarvis",
        threshold: float = 0.5,
        refractory_seconds: float = 2.0,
    ):
        from openwakeword.model import Model

        self.threshold = threshold
        self.refractory = refractory_seconds
        self._last_fire = 0.0

        wakeword_dir = MODELS_DIR / "openwakeword"
        candidates = list(wakeword_dir.glob(f"{model_name}*.onnx"))
        if not candidates:
            raise FileNotFoundError(
                f"No {model_name} model in {wakeword_dir}. "
                "Run: python scripts/download_models.py"
            )
        self.model = Model(
            wakeword_models=[str(candidates[0])],
            inference_framework="onnx",
            melspec_model_path=str(wakeword_dir / "melspectrogram.onnx"),
            embedding_model_path=str(wakeword_dir / "embedding_model.onnx"),
        )
        self.key = list(self.model.models.keys())[0]
        self.score = 0.0
        log.info("wake word ready: %s (threshold %.2f)", self.key, threshold)

    def process(self, frame: np.ndarray) -> bool:
        """Feed one frame of float32 mono @16k. True when the wake word fires."""
        pcm = np.clip(frame * 32767.0, -32768, 32767).astype(np.int16)
        scores = self.model.predict(pcm)
        self.score = float(scores.get(self.key, 0.0))

        if self.score < self.threshold:
            return False
        now = time.monotonic()
        if now - self._last_fire < self.refractory:
            return False
        self._last_fire = now
        self.model.reset()
        log.info("wake word fired (%.2f)", self.score)
        return True

    def reset(self) -> None:
        self.model.reset()
        self.score = 0.0
