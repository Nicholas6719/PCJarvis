"""The part of him that speaks first.

Almost every line of JARVIS anyone remembers is unprompted. He volunteers that
the compression in a cylinder is low, that the suit can fly, that the odds have
changed. He is a presence in the room rather than a prompt waiting for input,
and that difference is most of the character.

The machinery for speaking unprompted already existed and was already governed
properly -- never while muted, never over a reply in progress, never the same
thing twice, and it waits up to thirty seconds for a gap rather than cutting in.
What it lacked was anything to say. Only timers ever used it. This supplies the
rest.

The whole design problem here is restraint. An assistant that comments on
everything is worse than one that says nothing, because he learns to tune it
out, and then the one remark that mattered goes unheard too. So:

  * Every observation fires on a threshold being *crossed*, not on a threshold
    being exceeded. Sitting at 94% memory produces one remark, not one a minute.
  * Each one re-arms only after the condition has genuinely cleared, with a gap
    below the threshold so a value hovering on the line cannot chatter.
  * Each has a cooldown, so even a real recurrence stays quiet for a while.
  * Only the critical ones speak while he has dismissed JARVIS. If he has said
    goodnight, a remark about disk space can wait; a battery about to die
    cannot.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import history, quiet, schedules, standing
from .bus import BUS

log = logging.getLogger("jarvis.watch")


@dataclass
class Observation:
    """Something worth saying, unprompted."""
    id: str
    text: str
    critical: bool = False      # may speak even when he has dismissed JARVIS


@dataclass
class _Gate:
    """Per-observation state: has it fired, and when may it fire again."""
    armed: bool = True
    last_said: float = field(default=0.0)


class Watcher:
    """Watches quietly, speaks rarely."""

    def __init__(self, cfg, state_getter=None):
        self.cfg = cfg
        self._state_getter = state_getter
        self._gates: dict[str, _Gate] = {}
        self._cpu_window: list[float] = []
        self._started = time.time()
        self._last_activity = time.time()
        self._known_downloads: set[str] = set()
        self._downloads_primed = False
        self._arrived: list[str] = []
        self._due_protocols: list[str] = []
        self._running = False
        self._stop = None          # asyncio.Event, made inside the loop
        self._loop = None

    # ── plumbing ───────────────────────────────────────────────────
    def note_activity(self) -> None:
        """He said something. Used only by the long-session observation."""
        self._last_activity = time.time()

    def _gate(self, key: str) -> _Gate:
        return self._gates.setdefault(key, _Gate())

    def _fire(self, obs: Observation, cooldown_s: float) -> Observation | None:
        """Let an observation through only if it is genuinely new."""
        gate = self._gate(obs.id)
        if not gate.armed:
            return None
        if time.time() - gate.last_said < cooldown_s:
            return None
        gate.armed = False
        gate.last_said = time.time()
        return obs

    def _rearm(self, key: str) -> None:
        self._gate(key).armed = True

    # ── the observations ───────────────────────────────────────────
    def _check_power(self) -> list[Observation]:
        import psutil

        out = []
        battery = psutil.sensors_battery()
        if battery is None:
            return out

        percent = battery.percent
        plugged = bool(battery.power_plugged)
        low = self.cfg.get("watch.battery_low", 20)
        critical = self.cfg.get("watch.battery_critical", 10)

        # Plugged in means nothing to warn about, and it is also the moment to
        # re-arm: he has dealt with it.
        if plugged or percent > low + 8:
            self._rearm("battery_low")
        if plugged or percent > critical + 8:
            self._rearm("battery_critical")

        if not plugged and percent <= critical:
            got = self._fire(
                Observation("battery_critical",
                            f"Battery is at {percent:.0f}%. "
                            f"I would plug in now, sir.",
                            critical=True),
                cooldown_s=300)
            if got:
                out.append(got)
        elif not plugged and percent <= low:
            got = self._fire(
                Observation("battery_low",
                            f"Battery is down to {percent:.0f}%, "
                            f"and you are not plugged in."),
                cooldown_s=900)
            if got:
                out.append(got)
        return out

    def _check_storage(self) -> list[Observation]:
        import psutil

        out = []
        limit = self.cfg.get("watch.disk_full_percent", 92)
        try:
            usage = psutil.disk_usage(str(Path.home().anchor or "C:\\"))
        except Exception:
            return out

        if usage.percent < limit - 3:
            self._rearm("disk_full")
        if usage.percent >= limit:
            got = self._fire(
                Observation("disk_full",
                            f"The disk is at {usage.percent:.0f}%. "
                            f"Worth clearing some space."),
                cooldown_s=6 * 3600)
            if got:
                out.append(got)
        return out

    def _check_memory(self) -> list[Observation]:
        import psutil

        out = []
        limit = self.cfg.get("watch.memory_pressure_percent", 92)
        percent = psutil.virtual_memory().percent

        if percent < limit - 6:
            self._rearm("memory_pressure")
        if percent >= limit:
            got = self._fire(
                Observation("memory_pressure",
                            f"Memory is at {percent:.0f}%. "
                            f"Something will start to struggle shortly."),
                cooldown_s=1800)
            if got:
                out.append(got)
        return out

    def _check_processor(self) -> list[Observation]:
        """Sustained load only. A spike is normal and not worth a word."""
        import psutil

        out = []
        limit = self.cfg.get("watch.cpu_busy_percent", 85)
        minutes = self.cfg.get("watch.cpu_busy_minutes", 5)
        interval = max(self.cfg.get("watch.interval_s", 60), 1)
        needed = max(2, int(minutes * 60 / interval))

        self._cpu_window.append(psutil.cpu_percent(interval=None))
        del self._cpu_window[:-needed]

        sustained = (len(self._cpu_window) >= needed
                     and all(v >= limit for v in self._cpu_window))
        if not sustained:
            if self._cpu_window and self._cpu_window[-1] < limit - 15:
                self._rearm("cpu_busy")
            return out

        top = ""
        try:
            procs = sorted(psutil.process_iter(["name", "cpu_percent"]),
                           key=lambda p: p.info.get("cpu_percent") or 0,
                           reverse=True)
            if procs:
                name = (procs[0].info.get("name") or "").replace(".exe", "")
                if name:
                    top = f" {name} appears to be the culprit."
        except Exception:
            log.debug("could not identify the busy process", exc_info=True)

        got = self._fire(
            Observation("cpu_busy",
                        f"Something has been pinning the processor for "
                        f"{minutes} minutes.{top}"),
            cooldown_s=1800)
        if got:
            out.append(got)
        return out

    def _check_downloads(self) -> list[Observation]:
        """A file that has finished arriving.

        Partial downloads carry a suffix while they are in flight, so a file
        without one that was not there a minute ago has landed. The first pass
        only records what is already present -- otherwise he would announce the
        entire folder the moment he starts.
        """
        if not self.cfg.get("watch.downloads", True):
            return []

        out = []
        try:
            from .folders import save_folder

            folder = save_folder("downloads")
            if not folder.is_dir():
                return out

            partial = (".crdownload", ".part", ".tmp", ".download", ".opdownload")
            current = {p.name for p in folder.iterdir()
                       if p.is_file() and not p.name.lower().endswith(partial)}
        except Exception:
            log.debug("could not read the downloads folder", exc_info=True)
            return out

        if not self._downloads_primed:
            self._known_downloads = current
            self._downloads_primed = True
            return out

        arrived = sorted(current - self._known_downloads)
        self._known_downloads = current
        # Standing watches need to see these too, and this is the only place
        # that knows which files are new.
        self._arrived = arrived
        if not arrived:
            return out

        # Say one thing, however many landed.
        if len(arrived) == 1:
            text = f"{arrived[0]} has finished downloading."
        else:
            text = f"{len(arrived)} files have finished downloading."

        # Downloads are events rather than states, so this one re-arms itself.
        self._rearm("download_done")
        got = self._fire(Observation("download_done", text), cooldown_s=20)
        if got:
            out.append(got)
        return out

    def _check_session(self) -> list[Observation]:
        """How long he has been at it. The 'seventeen drinks' observation."""
        hours = self.cfg.get("watch.long_session_hours", 3)
        if hours <= 0:
            return []

        sitting = (time.time() - self._started) / 3600
        recent = (time.time() - self._last_activity) < 1800
        if sitting < hours or not recent:
            return []

        got = self._fire(
            Observation("long_session",
                        f"You have been at this for {int(sitting)} hours, sir."),
            cooldown_s=3 * 3600)
        if not got:
            return []
        # Re-arm so it can mention the next milestone, not the same one.
        self._rearm("long_session")
        return [got]

    # ── the loop ───────────────────────────────────────────────────
    def _collect(self) -> list[Observation]:
        found: list[Observation] = []
        self._arrived = []
        self._sample()
        for check in (self._check_power, self._check_storage,
                      self._check_memory, self._check_processor,
                      self._check_downloads, self._check_session):
            try:
                found.extend(check())
            except Exception:
                log.debug("watch check %s failed", check.__name__, exc_info=True)

        # Things he was asked to keep an eye on. Marked critical because he
        # was asked: a requested report is not the same as an unsolicited
        # remark, and silencing it during quiet hours would mean the answer to
        # "tell me when the build is done" is sometimes no.
        # Protocols whose moment has come. Run rather than announced -- the
        # point of scheduling one is that it happens without being discussed.
        try:
            for name in schedules.due():
                self._due_protocols.append(name)
        except Exception:
            log.debug("scheduled protocols failed", exc_info=True)

        try:
            for sentence in standing.check(self._arrived):
                found.append(Observation(f"standing:{sentence[:24]}", sentence,
                                         critical=True))
        except Exception:
            log.debug("standing watches failed", exc_info=True)
        return found

    def _sample(self) -> None:
        """One reading, for the history. Cheap, and never fatal."""
        try:
            import psutil

            battery = psutil.sensors_battery()
            history.record_app(history.foreground_app())
            history.record(
                cpu=self._cpu_window[-1] if self._cpu_window else None,
                memory=psutil.virtual_memory().percent,
                disk=psutil.disk_usage(str(Path.home().anchor or "C:")).percent,
                battery=battery.percent if battery else None,
                plugged=bool(battery.power_plugged) if battery else None)
        except Exception:
            log.debug("could not take a reading", exc_info=True)

    def _may_speak(self, obs: Observation) -> bool:
        """Three reasons to stay silent, in order of how specific they are.

        A snoozed observation is silenced outright, urgent or not: he asked
        for that one to stop and being overruled by the thing he just
        muted is worse than missing it. Quiet hours and a dismissed JARVIS
        both hold back the ordinary and let the urgent through -- a battery
        about to die at three in the morning is worth the interruption, a
        disk at 93% is not.
        """
        if quiet.snoozed(obs.id):
            return False
        if obs.critical:
            return True
        if quiet.active():
            quiet.defer(obs.id, obs.text)
            return False
        if self._state_getter is None:
            return True
        try:
            return getattr(self._state_getter(), "value", "") != "sleeping"
        except Exception:
            return True

    async def _wait(self, seconds: float) -> None:
        """Sleep, but wake the instant we are told to stop.

        A plain asyncio.sleep here made closing JARVIS hang. The loop opens
        by waiting two minutes for the machine to settle, and a stop arriving
        during that wait was not noticed until it expired -- so the window
        shut, the model unloaded, and the process sat there holding 1.2 GB
        for another hundred seconds before it would exit.
        """
        if self._stop is None:
            return
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def run(self) -> None:
        interval = max(float(self.cfg.get("watch.interval_s", 60)), 5.0)
        self._running = True
        self._stop = asyncio.Event()
        self._loop = asyncio.get_running_loop()
        log.info("ambient watch running, every %.0fs", interval)

        # Let the machine settle before forming an opinion of it. Boot is the
        # busiest the processor will be all session, and remarking on that
        # would be both wrong and the very first thing he ever said unprompted.
        await self._wait(min(interval * 2, 120))

        while self._running:
            try:
                for name in self._due_protocols:
                    log.info("running scheduled protocol: %s", name)
                    try:
                        from .tools.protocols import run_protocol

                        await run_protocol(name)
                    except Exception:
                        log.exception("scheduled protocol %s failed", name)
                self._due_protocols.clear()

                for obs in self._collect():
                    if not self._may_speak(obs):
                        log.debug("held back while dismissed: %s", obs.id)
                        continue
                    log.info("observation: %s", obs.text)
                    quiet.note_spoken(obs.id)
                    await BUS.emit("proactive", text=obs.text, source="watch")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("ambient watch stumbled; carrying on")
            await self._wait(interval)

    def stop(self) -> None:
        """Called from the shutdown path, which is not this loop's thread."""
        self._running = False
        if self._stop is not None and self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._stop.set)
            except RuntimeError:
                pass          # loop already closed; nothing left to wake
