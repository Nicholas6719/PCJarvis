"""PC control: apps, volume, brightness, power, windows, clipboard, telemetry.

This is the part that makes him feel like he actually lives in the machine
rather than in a chat window.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

import psutil

from ..config import CONFIG, ROOT
from .registry import tool

log = logging.getLogger("jarvis.tools.system")

# Spoken names that do not match their executable.
APP_ALIASES = {
    "vs code": "code", "vscode": "code", "visual studio code": "code",
    "browser": "brave", "chrome": "chrome", "brave browser": "brave",
    "file explorer": "explorer", "files": "explorer", "explorer": "explorer",
    "task manager": "taskmgr", "control panel": "control",
    "terminal": "wt", "windows terminal": "wt", "command prompt": "cmd",
    "powershell": "powershell", "notepad": "notepad", "calculator": "calc",
    "settings": "ms-settings:", "spotify": "spotify", "paint": "mspaint",
    "snipping tool": "snippingtool", "camera": "microsoft.windows.camera:",
}

_START_MENU_DIRS = [
    Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
]


# ══════════════════════════════════════════════════════════════════
#  Applications
# ══════════════════════════════════════════════════════════════════
def _find_shortcut(name: str) -> Path | None:
    """Search the Start Menu for a .lnk whose name looks like what he said."""
    target = name.lower().strip()
    best: tuple[int, Path] | None = None
    for root in _START_MENU_DIRS:
        if not root.exists():
            continue
        for lnk in root.rglob("*.lnk"):
            stem = lnk.stem.lower()
            if stem == target:
                return lnk
            if target in stem:
                score = len(stem) - len(target)  # prefer the tightest match
                if best is None or score < best[0]:
                    best = (score, lnk)
    return best[1] if best else None


@tool(category="system")
def open_app(name: str) -> str:
    """Launch an application by name.

    Args:
        name: The application to open, e.g. "Spotify", "VS Code", "Brave".
    """
    key = name.lower().strip()
    command = APP_ALIASES.get(key, key)

    # URI-protocol apps (Settings, Camera) go through the shell.
    if command.endswith(":"):
        os.startfile(command)
        return f"Opened {name}."

    if shutil.which(command):
        subprocess.Popen(command, shell=True,
                         creationflags=subprocess.CREATE_NEW_CONSOLE
                         if command in ("cmd", "powershell") else 0)
        return f"Opened {name}."

    shortcut = _find_shortcut(name)
    if shortcut:
        os.startfile(str(shortcut))
        return f"Opened {shortcut.stem}."

    return f"I couldn't find an application called {name}."


@tool(category="system")
def close_app(name: str) -> str:
    """Close a running application by name.

    Args:
        name: The application to close, e.g. "Notepad", "Spotify".
    """
    target = APP_ALIASES.get(name.lower().strip(), name.lower().strip())
    killed = 0
    for proc in psutil.process_iter(["name"]):
        pname = (proc.info["name"] or "").lower()
        if pname.startswith(target) or pname == f"{target}.exe":
            try:
                proc.terminate()
                killed += 1
            except psutil.Error:
                pass
    if not killed:
        return f"{name} does not appear to be running."
    return f"Closed {name}."


@tool(category="system")
def list_running_apps() -> str:
    """List the applications currently running with visible windows."""
    try:
        import pygetwindow as gw
        titles = [t for t in gw.getAllTitles() if t.strip()]
        if not titles:
            return "Nothing with a visible window."
        return "Open windows: " + "; ".join(titles[:15])
    except Exception as e:
        return f"Could not enumerate windows: {e}"


@tool(category="system")
def focus_window(title: str) -> str:
    """Bring a window to the foreground.

    Args:
        title: Part of the window title to match.
    """
    try:
        import pygetwindow as gw
        matches = [w for w in gw.getAllWindows()
                   if title.lower() in w.title.lower() and w.title.strip()]
        if not matches:
            return f"No window matching {title}."
        w = matches[0]
        if w.isMinimized:
            w.restore()
        w.activate()
        return f"Focused {w.title}."
    except Exception as e:
        return f"Could not focus that window: {e}"


# ══════════════════════════════════════════════════════════════════
#  Audio
# ══════════════════════════════════════════════════════════════════
def _com_init() -> None:
    """Initialise COM on the calling thread.

    Tools run via asyncio.to_thread, so they land on arbitrary worker threads
    from the pool. COM is per-thread: whichever thread happens to go first
    works, and the rest fail with "CoInitialize has not been called". That makes
    volume and media control intermittent in a way that looks like a hardware
    fault rather than a threading bug.
    """
    import comtypes

    try:
        comtypes.CoInitialize()
    except Exception:
        pass  # already initialised on this thread, which is fine


def _volume_interface():
    """The master volume control.

    pycaw changed shape: newer builds hand back an AudioDevice that exposes
    EndpointVolume directly, older ones return a raw COM object that has to be
    Activated. Support both, newest first.
    """
    _com_init()
    from pycaw.pycaw import AudioUtilities

    speakers = AudioUtilities.GetSpeakers()

    endpoint = getattr(speakers, "EndpointVolume", None)
    if endpoint is not None:
        return endpoint

    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import IAudioEndpointVolume

    interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return interface.QueryInterface(IAudioEndpointVolume)


@tool(category="system")
def set_volume(level: int) -> str:
    """Set the master output volume.

    Args:
        level: Volume percentage from 0 to 100.
    """
    level = max(0, min(100, int(level)))
    try:
        _volume_interface().SetMasterVolumeLevelScalar(level / 100.0, None)
        return f"Volume at {level} percent."
    except Exception as e:
        return f"Could not change the volume: {e}"


@tool(category="system")
def get_volume() -> str:
    """Report the current output volume level."""
    try:
        v = _volume_interface()
        pct = round(v.GetMasterVolumeLevelScalar() * 100)
        return f"Volume is at {pct} percent{' and muted' if v.GetMute() else ''}."
    except Exception as e:
        return f"Could not read the volume: {e}"


@tool(category="system")
def set_mute(muted: bool) -> str:
    """Mute or unmute the system audio.

    Args:
        muted: True to mute, False to unmute.
    """
    try:
        _volume_interface().SetMute(bool(muted), None)
        return "Muted." if muted else "Unmuted."
    except Exception as e:
        return f"Could not change mute state: {e}"


# ══════════════════════════════════════════════════════════════════
#  Display
# ══════════════════════════════════════════════════════════════════
@tool(category="system")
def set_brightness(level: int) -> str:
    """Set the screen brightness.

    Args:
        level: Brightness percentage from 0 to 100.
    """
    level = max(0, min(100, int(level)))
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
             f".WmiSetBrightness(1,{level})"],
            capture_output=True, timeout=10, check=True,
        )
        return f"Brightness at {level} percent."
    except Exception as e:
        return f"Could not set brightness: {e}"


@tool(category="system")
def take_screenshot(location: str = "") -> str:
    """Capture the screen and save it.

    Args:
        location: Where to put it -- "desktop", "documents", "downloads", or a
            folder path. Pass whatever he asked for. Defaults to Pictures.
    """
    try:
        import pyautogui

        from .documents import _resolve_dir

        problem = ""
        if location.strip():
            folder, spoken_where, problem = _resolve_dir(location)
        else:
            folder = Path.home() / "Pictures" / "JARVIS"
            spoken_where = "your Pictures, JARVIS folder"
        folder.mkdir(parents=True, exist_ok=True)

        path = folder / f"screen_{time.strftime('%Y%m%d_%H%M%S')}.png"
        shot = pyautogui.screenshot()

        # Windows refuses to capture the secure desktop, so a locked
        # session -- or a display that has powered down -- comes back as
        # pure black on every capture path there is. The file still
        # writes and still has a plausible size, and announcing
        # 'screenshot saved, 7 KB' for a black rectangle is the same
        # confident lie as announcing a PDF that was never created.
        # One uniform colour across the whole frame does not happen by
        # accident on a screen in use.
        low, high = shot.convert('L').getextrema()
        blank = low == high
        shot.save(path)

        # Confirm from the file on disk. Asked to save one to the Desktop, an
        # earlier version put it in Pictures and let the model announce the
        # Desktop -- true from the tool's point of view, useless from his.
        if not path.exists():
            return "I took the screenshot but could not save it."
        size = path.stat().st_size
        if problem:
            return f"{problem}. It is saved as {path.name}."
        if blank:
            return (f"That came out completely blank. The screen was locked "
                    f"or switched off, and Windows will not capture either. "
                    f"I saved it as {path.name} on {spoken_where}, but there "
                    f"is nothing in it -- unlock the screen and ask me again.")
        return (f"Screenshot saved as {path.name} on {spoken_where} "
                f"({size/1024:.0f} KB).")
    except Exception as e:
        return f"Screenshot failed: {e}"


# ══════════════════════════════════════════════════════════════════
#  Telemetry
# ══════════════════════════════════════════════════════════════════
@tool(category="system")
def get_system_stats(component: str = "all") -> str:
    """Report system status. Ask for one component when only one was requested.

    Args:
        component: "cpu", "memory", "disk", "battery", or "all". If he asked
            about the CPU, pass "cpu" -- do not read out everything.
    """
    which = (component or "all").lower().strip()
    if which in ("ram", "mem"):
        which = "memory"
    if which in ("storage", "drive", "space"):
        which = "disk"
    if which in ("power", "charge"):
        which = "battery"

    if which == "cpu":
        return f"CPU is at {psutil.cpu_percent(interval=0.4):.0f}%."
    if which == "memory":
        mem = psutil.virtual_memory()
        return (f"Memory is at {mem.percent:.0f}%, using {mem.used/1e9:.1f} GB "
                f"of {mem.total/1e9:.0f} GB.")
    if which == "disk":
        disk = psutil.disk_usage("C:/")
        return (f"The C drive has {disk.free/1e9:.0f} GB free of "
                f"{disk.total/1e9:.0f} GB.")
    if which == "battery":
        return get_battery()

    cpu = psutil.cpu_percent(interval=0.4)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("C:/")
    parts = [
        f"CPU at {cpu:.0f}%",
        f"memory at {mem.percent:.0f}% ({mem.used/1e9:.1f} of "
        f"{mem.total/1e9:.0f} GB)",
        f"C drive with {disk.free/1e9:.0f} GB free",
    ]
    battery = psutil.sensors_battery()
    if battery:
        parts.append(f"battery at {battery.percent:.0f}%, "
                     f"{'charging' if battery.power_plugged else 'on battery'}")
    return "; ".join(parts) + "."


@tool(category="system")
def get_battery() -> str:
    """Report the battery percentage and whether it is charging."""
    battery = psutil.sensors_battery()
    if not battery:
        return "No battery detected."

    # Stated flatly and only once. An earlier version said "at 100 percent and
    # charging" and the model relayed it as "fully charged but not currently
    # being charged" -- the opposite of the truth. Short, unambiguous sentences
    # survive paraphrasing.
    if battery.power_plugged:
        if battery.percent >= 99:
            return "Battery: 100%. Plugged in and charging."
        return f"Battery: {battery.percent:.0f}%. Plugged in and charging."

    text = f"Battery: {battery.percent:.0f}%. Running on battery"
    if battery.secsleft and battery.secsleft > 0:
        hours, minutes = divmod(int(battery.secsleft) // 60, 60)
        text += f", about {hours} hours {minutes} minutes left"
    return text + "."


@tool(category="system")
def get_time() -> str:
    """Report the current date and time."""
    now = time.localtime()
    return time.strftime("It is %I:%M %p on %A, the %d of %B %Y.", now).replace(
        " 0", " "
    )


# ══════════════════════════════════════════════════════════════════
#  Clipboard
# ══════════════════════════════════════════════════════════════════
@tool(category="system")
def read_clipboard() -> str:
    """Read the current contents of the clipboard."""
    try:
        import pyperclip
        text = pyperclip.paste()
        return f"Clipboard contains: {text[:1500]}" if text else "Clipboard is empty."
    except Exception as e:
        return f"Could not read the clipboard: {e}"


@tool(category="system")
def write_clipboard(text: str) -> str:
    """Copy text to the clipboard.

    Args:
        text: The text to place on the clipboard.
    """
    try:
        import pyperclip
        pyperclip.copy(text)
        return "Copied to your clipboard."
    except Exception as e:
        return f"Could not write to the clipboard: {e}"


# ══════════════════════════════════════════════════════════════════
#  Power and shell -- gated
# ══════════════════════════════════════════════════════════════════
@tool(category="system")
def lock_screen() -> str:
    """Lock the workstation."""
    subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"], check=False)
    return "Locking now."


@tool(category="system", destructive=True)
def sleep_computer() -> str:
    """Suspend the COMPUTER itself. Only for an explicit request to sleep the
    machine -- "go to sleep" on its own means JARVIS should stand down, not
    that the laptop should suspend."""
    # rundll32 powrprof.dll,SetSuspendState quietly does nothing on machines
    # where hibernation is disabled, and reports success either way. Calling
    # the API directly actually suspends.
    try:
        import ctypes

        ok = ctypes.windll.powrprof.SetSuspendState(False, False, False)
        if ok:
            return "Suspending the machine."
    except Exception as e:
        log.warning("SetSuspendState failed: %s", e)

    r = subprocess.run(["shutdown", "/h"], capture_output=True, check=False)
    if r.returncode == 0:
        return "Suspending the machine."
    return ("I could not suspend the machine. Sleep may be disabled in your "
            "power settings.")


@tool(category="system", destructive=True)
def shutdown_computer(restart: bool = False) -> str:
    """Shut down or restart the computer.

    Args:
        restart: True to restart instead of shutting down.
    """
    subprocess.run(["shutdown", "/r" if restart else "/s", "/t", "20"], check=False)
    action = "Restarting" if restart else "Shutting down"
    return f"{action} in twenty seconds. Say cancel shutdown to stop it."


@tool(category="system")
def cancel_shutdown() -> str:
    """Cancel a pending shutdown or restart."""
    r = subprocess.run(["shutdown", "/a"], capture_output=True, check=False)
    return "Shutdown cancelled." if r.returncode == 0 else "There was none pending."


@tool(category="system", destructive=True, speak_while_running=True)
def run_command(command: str) -> str:
    """Run a PowerShell command on this machine and return its output.

    Use only when no other tool will do. Requires spoken confirmation.

    Args:
        command: The PowerShell command to execute.
    """
    if not CONFIG.get("tools.shell_enabled", True):
        return "Shell access is disabled in my configuration."
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=60, cwd=str(ROOT),
        )
        out = (r.stdout or "").strip() or (r.stderr or "").strip()
        return out[:2000] if out else "The command completed with no output."
    except subprocess.TimeoutExpired:
        return "That command took too long and I stopped it."
    except Exception as e:
        return f"Command failed: {e}"
