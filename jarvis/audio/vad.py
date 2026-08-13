"""Silero VAD, driven directly through onnxruntime.

The pip package pulls in torch (~2GB), which is absurd for a 2MB model on a
laptop with no CUDA, so we run the ONNX graph ourselves.

The one non-obvious requirement, and the source of a bug that made JARVIS
appear deaf: silero v5 is a *streaming* model that needs the last 64 samples of
the previous chunk prepended to each 512-sample window, giving 576 inputs. Feed
it a bare 512 and it does not complain -- no exception, no warning -- it simply
returns ~0.001 for every frame forever, including shouted speech. Measured:

    512 alone     speech max 0.003   silence max 0.002   (useless)
    64 + 512      speech max 1.000   silence max 0.027   (correct)
"""
from __future__ import annotations

import logging

import numpy as np
import onnxruntime as ort

from ..config import MODELS_DIR

log = logging.getLogger("jarvis.vad")

WINDOW = 512      # samples of new audio per inference, at 16kHz
CONTEXT = 64      # samples carried over from the previous window


class SileroVAD:
    def __init__(self, threshold: float = 0.5, sample_rate: int = 16000):
        path = MODELS_DIR / "silero_vad.onnx"
        if not path.exists():
            raise FileNotFoundError(
                f"{path} missing. Run: python scripts/download_models.py"
            )
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        self.session = ort.InferenceSession(
            str(path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.input_names = {i.name for i in self.session.get_inputs()}
        self.threshold = threshold
        self.sample_rate = sample_rate
        self.reset()

    def reset(self) -> None:
        # v5 carries a single combined LSTM state; v4 split it into h and c.
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._h = np.zeros((2, 1, 64), dtype=np.float32)
        self._c = np.zeros((2, 1, 64), dtype=np.float32)
        self._context = np.zeros(CONTEXT, dtype=np.float32)
        self._tail = np.zeros(0, dtype=np.float32)

    def _infer(self, window: np.ndarray) -> float:
        # Prepend the carried context -- without this the model returns a
        # constant and the whole listener goes deaf.
        payload = np.concatenate([self._context, window]).astype(np.float32)

        feeds: dict[str, np.ndarray] = {
            "input": payload.reshape(1, -1),
            "sr": np.array(self.sample_rate, dtype=np.int64),
        }
        if "state" in self.input_names:
            feeds["state"] = self._state
        else:  # older v4 graph
            feeds["h"] = self._h
            feeds["c"] = self._c

        out = self.session.run(None, feeds)
        prob = float(out[0].squeeze())

        if "state" in self.input_names:
            self._state = out[1]
        else:
            self._h, self._c = out[1], out[2]

        self._context = window[-CONTEXT:].copy()
        return prob

    def probability(self, frame: np.ndarray) -> float:
        """Speech probability for an arbitrary-length frame.

        Frames arrive as 1280 samples but the model consumes 512 at a time, so
        we buffer and return the highest probability across the complete windows
        contained in this frame.
        """
        buf = np.concatenate([self._tail, frame.astype(np.float32)])
        n = (len(buf) // WINDOW) * WINDOW
        self._tail = buf[n:]
        if n == 0:
            return 0.0
        return max(self._infer(buf[i:i + WINDOW]) for i in range(0, n, WINDOW))

    def is_speech(self, frame: np.ndarray) -> bool:
        return self.probability(frame) >= self.threshold
