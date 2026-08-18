"""Machine health: reclaiming leaked memory, and refusing to start blind.

This module exists because of a genuinely nasty failure discovered by measuring
rather than reasoning.

Ollama does not host the model itself -- it spawns `llama-server.exe`, which
holds the weights (~3.9 GB on this machine, more in shared GPU memory). When
Ollama is restarted, killed, or crashes, that child is routinely **orphaned**:
it keeps running, keeps holding every byte, and has no parent left to reap it.

They accumulate. Four of them were found on this laptop holding 3.9 GB between
them, with the machine sitting at 92% memory and 1.3 GB free -- which is exactly
the condition under which the next launch crashes, orphaning another one. A
death spiral that looks like "JARVIS keeps crashing" and is really "the last
crash left four gigabytes behind".

Killing them recovered 7.56 GB and took the machine from 92% to 40%.

So: reap orphans at every startup, and report honestly on what is left.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("jarvis.health")

# The processes Ollama spawns to hold model weights.
MODEL_HOSTS = {"llama-server.exe", "ollama_llama_server.exe"}
SUPERVISORS = {"ollama.exe", "ollama app.exe"}


def reap_orphaned_model_hosts() -> tuple[int, float]:
    """Kill model-host processes whose supervisor is gone.

    Returns (count, gigabytes_freed). Never raises -- a failure to tidy up must
    not stop JARVIS starting.
    """
    try:
        import psutil
    except Exception:
        return 0, 0.0

    try:
        supervisors = {
            p.pid for p in psutil.process_iter(["name"])
            if (p.info["name"] or "").lower() in SUPERVISORS
        }

        orphans = []
        for proc in psutil.process_iter(["name", "ppid", "memory_info"]):
            try:
                if (proc.info["name"] or "").lower() not in MODEL_HOSTS:
                    continue
                parent = proc.info["ppid"]
                # An orphan is one whose parent has died. On Windows the ppid
                # is not recycled to init, so a dead parent means abandoned.
                if parent in supervisors or psutil.pid_exists(parent):
                    continue
                orphans.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if not orphans:
            return 0, 0.0

        freed = 0.0
        killed = 0
        for proc in orphans:
            try:
                freed += (proc.info["memory_info"].rss if proc.info["memory_info"]
                          else 0)
                proc.kill()
                killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if killed:
            time.sleep(1.0)     # let Windows actually release the pages
            log.warning(
                "reclaimed %.2f GB from %d orphaned model host%s left behind by "
                "a previous run", freed / 1e9, killed, "" if killed == 1 else "s")
        return killed, freed / 1e9
    except Exception:
        log.debug("orphan reaping failed", exc_info=True)
        return 0, 0.0


def reap_orphaned_webviews() -> tuple[int, float]:
    """Kill WebView2 helpers left behind by a previous JARVIS.

    Deliberately narrow. WebView2 is shared infrastructure: sampling this
    machine found helpers belonging to SearchHost.exe and Widgets.exe -- the
    Start menu and the widgets panel. Killing those on a "looks orphaned"
    heuristic would break parts of Windows.

    So both conditions must hold: the process must name JARVIS.exe as its host
    via --webview-exe-name, AND its parent must be gone. That combination can
    only be our own debris.
    """
    try:
        import psutil
    except Exception:
        return 0, 0.0

    killed, freed = 0, 0.0
    try:
        for proc in psutil.process_iter(["name", "ppid", "cmdline",
                                         "memory_info"]):
            try:
                if (proc.info["name"] or "").lower() != "msedgewebview2.exe":
                    continue
                cmdline = " ".join(proc.info["cmdline"] or []).lower()
                if "--webview-exe-name=jarvis.exe" not in cmdline:
                    continue
                if psutil.pid_exists(proc.info["ppid"]):
                    continue
                freed += (proc.info["memory_info"].rss
                          if proc.info["memory_info"] else 0)
                proc.kill()
                killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if killed:
            log.info("cleaned up %d orphaned WebView2 helper%s (%.0f MB)",
                     killed, "" if killed == 1 else "s", freed / 1e6)
    except Exception:
        log.debug("webview reaping failed", exc_info=True)
    return killed, freed / 1e9


def stop_ollama(timeout: float = 6.0) -> tuple[bool, float]:
    """Shut Ollama down and release the model's memory.

    Order matters enormously here. Killing ollama.exe on its own orphans the
    llama-server child that actually holds the weights -- which is precisely
    the leak that filled this machine four times over. So:

      1. ask Ollama to unload the model (keep_alive=0), the graceful path
      2. terminate the llama-server children explicitly
      3. only then stop the supervisors

    Returns (stopped_anything, gigabytes_freed).
    """
    try:
        import psutil
    except Exception:
        return False, 0.0

    before = psutil.virtual_memory().available

    # 1. Ask nicely: this lets Ollama free the weights itself.
    try:
        import httpx

        with httpx.Client(timeout=3.0) as client:
            listing = client.get("http://127.0.0.1:11434/api/tags").json()
            for model in listing.get("models", []):
                name = model.get("model") or model.get("name")
                if name:
                    client.post("http://127.0.0.1:11434/api/generate",
                                json={"model": name, "keep_alive": 0})
        time.sleep(1.0)
    except Exception:
        log.debug("graceful unload skipped", exc_info=True)

    # 2. Children first, so nothing can be orphaned.
    stopped = 0
    for names in (MODEL_HOSTS, SUPERVISORS):
        victims = []
        for proc in psutil.process_iter(["name"]):
            try:
                if (proc.info["name"] or "").lower() in names:
                    victims.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        for proc in victims:
            try:
                proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        gone, alive = psutil.wait_procs(victims, timeout=timeout / 2)
        for proc in alive:
            try:
                proc.kill()          # it had its chance
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        stopped += len(victims)

    if stopped:
        time.sleep(1.0)
        freed = (psutil.virtual_memory().available - before) / 1e9
        log.info("stopped Ollama (%d process%s), released %.2f GB",
                 stopped, "" if stopped == 1 else "es", max(freed, 0.0))
        return True, max(freed, 0.0)
    return False, 0.0


def memory_report() -> dict:
    """Current memory state, and whether it is tight enough to matter."""
    try:
        import psutil

        m = psutil.virtual_memory()
        return {
            "percent": m.percent,
            "available_gb": m.available / 1e9,
            "total_gb": m.total / 1e9,
            # Below ~2 GB free this machine starts thrashing, and loading a
            # 5 GB model on top of that is how a launch turns into a crash.
            "tight": m.available < 2.2e9,
        }
    except Exception:
        return {"percent": 0.0, "available_gb": 0.0, "total_gb": 0.0,
                "tight": False}


def _read_mic_mute() -> bool | None:
    """The actual COM work. Never called on the main thread."""
    import comtypes
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    comtypes.CoInitialize()
    try:
        for device in AudioUtilities.GetAllDevices():
            name = str(getattr(device, 'FriendlyName', '') or '')
            if 'Microphone' not in name:
                continue
            if str(getattr(device, 'state', '')).endswith('Active'):
                iface = device._dev.Activate(
                    IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                return bool(iface.QueryInterface(IAudioEndpointVolume).GetMute())
    finally:
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass
    return None


def microphone_muted() -> bool | None:
    """Is the capture device muted? None if it cannot be determined.

    Worth checking at all because a muted microphone is indistinguishable,
    from where he is sitting, from a broken wake word: the device opens,
    reports itself healthy, and returns digital silence forever. That cost an
    evening of hunting for a fault in code that was working perfectly.

    Run on its own thread, and this is not fussiness. The first version
    called CoInitialize on the boot thread, which is the same thread the
    microphone stream is opened from moments later -- and the wake word then
    stopped firing entirely. COM is per-thread, and this project has already
    learned that once each for the media session, the location service and
    OCR. A diagnostic that breaks the thing it is diagnosing is worse than no
    diagnostic at all.
    """
    try:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_read_mic_mute).result(timeout=8)
    except Exception:
        log.debug('could not read the microphone mute state', exc_info=True)
        return None

def startup_check() -> dict:
    """Reap, then report. Called before anything heavy is loaded."""
    killed, freed = reap_orphaned_model_hosts()
    wv_killed, wv_freed = reap_orphaned_webviews()
    killed += wv_killed
    freed += wv_freed
    report = memory_report()
    report["reclaimed_gb"] = freed
    report["orphans_killed"] = killed

    if report["tight"]:
        log.warning(
            "only %.1f GB of memory free (%.0f%% used). Model loading may be "
            "slow or unstable; close something if JARVIS misbehaves.",
            report["available_gb"], report["percent"])
    else:
        log.info("memory: %.1f GB free of %.0f GB (%.0f%% used)",
                 report["available_gb"], report["total_gb"], report["percent"])

    # What we actually resolved, recorded at boot because both of these
    # have moved under us without warning. The default microphone changes
    # the moment a headset connects, and OneDrive redirected the Desktop,
    # so files saved 'to the Desktop' landed somewhere never seen. Neither
    # failure announced itself; a line in the log would have.
    try:
        from .audio.mic import list_input_devices
        from .audio.player import list_output_devices

        mic = next((d['name'] for d in list_input_devices()
                    if d['default']), 'unknown')
        speaker = next((d['name'] for d in list_output_devices()
                        if d['default']), 'unknown')
        report['input_device'] = mic
        report['output_device'] = speaker
        log.info('audio in: %s', mic)

        muted = microphone_muted()
        report['mic_muted'] = muted
        if muted:
            log.warning('THE MICROPHONE IS MUTED -- he will not be heard')
        log.info('audio out: %s', speaker)
    except Exception:
        log.debug('could not enumerate audio devices', exc_info=True)

    try:
        from .folders import describe

        resolved = describe()
        report['folders'] = resolved
        log.info('desktop resolves to %s', resolved.get('desktop'))
    except Exception:
        log.debug('could not resolve shell folders', exc_info=True)

    return report
