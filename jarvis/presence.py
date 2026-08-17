"""Whether anyone is actually there.

He was announcing things to an empty room and counting them as delivered. A
download that finished while you were downstairs was reported once, to nobody,
and then never mentioned again -- which is worse than not having the feature,
because you were told it would tell you.

Windows knows when the keyboard and mouse were last touched, to the
millisecond, for nothing. That is the whole primary mechanism.

Its one blind spot is the reason the camera option exists: reading something on
screen for twenty minutes looks exactly like having left the building. The
keyboard cannot tell those apart and a camera can, which is why the check is
offered -- but it is off unless deliberately switched on, it never stores an
image, and it answers exactly one question, which is whether a face is in
front of the machine. See camera_check.
"""
from __future__ import annotations

import ctypes
import logging
import time

log = logging.getLogger("jarvis.presence")


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds() -> float:
    """Seconds since the last keyboard or mouse input. 0.0 if unknown."""
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        return max(0.0, (ctypes.windll.kernel32.GetTickCount()
                         - info.dwTime) / 1000.0)
    except Exception:
        log.debug("could not read the idle time", exc_info=True)
        return 0.0


class Presence:
    """Tracks whether he is at the machine, and notices the moment he returns.

    Deliberately asymmetric. Deciding he has left takes minutes of silence,
    because a pause to read or think is not an absence and treating it as one
    means everything gets held back constantly. Deciding he is back is
    instant: one keystroke is unambiguous.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self._present = True
        self._left_at = 0.0
        self._returned = False

    @property
    def away_seconds(self) -> float:
        if self._present or not self._left_at:
            return 0.0
        return time.time() - self._left_at

    def present(self) -> bool:
        # Switched off means always present, so nothing is ever held back.
        # Failing open matters here: the failure mode of getting this wrong is
        # a silent assistant, and silence looks identical to working properly.
        if not self.cfg.get("presence.enabled", True):
            return True
        return self._present

    def update(self) -> None:
        """Call on the watch tick. Sets the returned flag on a transition."""
        if not self.cfg.get("presence.enabled", True):
            return
        threshold = float(self.cfg.get("presence.away_after_s", 300))
        idle = idle_seconds()

        if self._present:
            if idle >= threshold:
                if self._camera_says_present():
                    return          # sitting there reading, not gone
                self._present = False
                # He went quiet when the idle clock started, not now.
                self._left_at = time.time() - idle
                log.info("he appears to have stepped away")
            return

        # Away by the keyboard. If the camera check is switched on, ask it
        # once before settling on that -- reading on screen for twenty minutes
        # is indistinguishable from an empty chair, and this is the only thing
        # that can tell them apart. Off by default; see jarvis/camera.py.
        if idle < threshold:
            self._present = True
            self._returned = True
            log.info("he is back after %.0f minutes",
                     (time.time() - self._left_at) / 60 if self._left_at else 0)

    def _camera_says_present(self) -> bool:
        """Only ever consulted at the moment he would be written off.

        Returns False unless the camera positively saw someone, so an
        unavailable camera, a disabled one, or any error at all leaves the
        keyboard in charge rather than silently changing the answer.
        """
        try:
            from . import camera

            return camera.looks_present(self.cfg) is True
        except Exception:
            log.debug("camera check failed", exc_info=True)
            return False

    def take_return(self) -> bool:
        """True exactly once per return, so a briefing cannot repeat."""
        if not self._returned:
            return False
        self._returned = False
        return True
