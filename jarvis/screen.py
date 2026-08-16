"""Reading what is actually on the screen.

This was written off as impossible. A vision model alongside the 7B does not
fit in 15 GB, so "what does this say" was answered from the window title and
nothing else -- the assistant equivalent of reading the spine of a book.

Windows has shipped an OCR engine since 8.1. It is part of the operating
system, it is reachable from Python through winsdk, and it costs nothing: no
download, no model resident in memory, no second process. Verified on this
machine before any of this was written -- en-US present, engine loads from the
user profile.

It reads text. It does not describe pictures, and it will not tell you a photo
is of a dog. For the thing actually being asked for most of the time -- what
does this error say, what is on this page, read me that -- text is the whole
answer.

Two failure modes are worth knowing about. A locked screen captures as pure
black on every Windows capture path, so this returns nothing and says why
rather than reporting an empty screen as an empty page. And OCR on a busy
desktop returns a mess of fragments from every window at once, which is why
reading a single window is the default and the full screen has to be asked
for.
"""
from __future__ import annotations

import logging

log = logging.getLogger("jarvis.screen")

# Below this the "text" is almost always interface furniture -- clock, tab
# labels, a menu bar -- rather than anything worth reading aloud.
MIN_USEFUL_CHARS = 12


def _engine():
    from winsdk.windows.media.ocr import OcrEngine

    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError("no OCR language is installed")
    return engine


async def _read_bitmap(image) -> str:
    """OCR one PIL image. Returns the text, joined into lines."""
    import io

    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.storage.streams import (DataWriter,
                                                InMemoryRandomAccessStream)

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="BMP")
    raw = buffer.getvalue()

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream.get_output_stream_at(0))
    writer.write_bytes(raw)
    await writer.store_async()
    await writer.flush_async()

    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()

    result = await _engine().recognize_async(bitmap)
    lines = [line.text.strip() for line in result.lines if line.text.strip()]
    return "\n".join(lines)


def _run(coro):
    """WinRT is apartment-bound, so give it a thread of its own every time.

    The same lesson as the media session and the location service: reusing the
    calling thread works once and then quietly returns nothing.
    """
    import asyncio
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro())).result(timeout=30)


def _capture(whole_screen: bool):
    """A PIL image of the foreground window, or of everything."""
    import pyautogui

    if whole_screen:
        return pyautogui.screenshot()

    try:
        import pygetwindow as gw

        window = gw.getActiveWindow()
        if window is not None and window.width > 40 and window.height > 40:
            left = max(window.left, 0)
            top = max(window.top, 0)
            return pyautogui.screenshot(
                region=(left, top, window.width, window.height))
    except Exception:
        log.debug("could not find the foreground window", exc_info=True)
    return pyautogui.screenshot()


def read(whole_screen: bool = False) -> tuple[str, str]:
    """(text, problem). Never raises."""
    try:
        image = _capture(whole_screen)
    except Exception as e:
        return "", f"I could not capture the screen: {e}"

    try:
        import numpy as np

        pixels = np.asarray(image.convert("L"))
        if int(pixels.max()) == int(pixels.min()):
            # Same guard as the screenshot tool: Windows refuses to capture
            # the secure desktop, and a locked screen comes back uniformly
            # black on every path there is.
            return "", ("The screen is locked or switched off, so there is "
                        "nothing for me to read.")
    except Exception:
        log.debug("could not check whether the capture was blank", exc_info=True)

    try:
        text = _run(lambda: _read_bitmap(image))
    except Exception as e:
        log.debug("OCR failed", exc_info=True)
        return "", f"I could not read the screen: {e}"

    if len(text.strip()) < MIN_USEFUL_CHARS:
        return "", ("I can see the screen, but there is no readable text on "
                    "it worth repeating.")
    return text.strip(), ""


def window_name() -> str:
    try:
        import pygetwindow as gw

        window = gw.getActiveWindow()
        return (window.title or "").strip() if window else ""
    except Exception:
        return ""
