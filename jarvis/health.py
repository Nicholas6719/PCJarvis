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
    return report
