"""Asking the camera one question, rarely, and only if told it may.

The keyboard has one blind spot: reading something on screen for twenty minutes
looks exactly like having left the building. A camera can tell those apart, and
that is the only reason this exists.

It is also the most invasive thing in this entire project, so the constraints
are not preferences and are not configurable away:

  * **Off unless switched on.** It ships disabled. Nothing here runs until
    `camera.enabled` is set deliberately, by hand, in config.yaml.
  * **Never stores anything.** The frame is decoded in memory, counted, and
    dropped. Nothing reaches disk, no image is kept in a variable that outlives
    the function, and there is no code path here that could write one.
  * **Answers one question.** Is there a face in front of the machine. Not
    whose, not how many people, not what they are doing. It uses the face
    *detector*, which finds face-shaped regions; there is no recognition
    anywhere in this file and no identity is computed.
  * **Only when already believed absent.** The keyboard is the primary signal.
    This runs only after several minutes of no input, which makes it rare by
    construction rather than by good intentions.
  * **Rate limited regardless.** At most one check every few minutes even if
    something upstream misbehaves.
  * **Announces itself in the log**, every time, so there is a record of
    exactly when the camera was used and how it answered.

The camera light will come on for the moment it is in use. That is a feature.
Hardware honesty is worth more than any assurance in a docstring, including
this one.

A failure of any kind returns None, meaning "no opinion", and presence falls
back to the keyboard alone. Guessing "present" would defeat the feature and
guessing "absent" would silence him wrongly; declining to answer does neither.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("jarvis.camera")

_last_check = 0.0
MIN_SECONDS_BETWEEN = 240


def enabled(cfg) -> bool:
    return bool(cfg.get("camera.enabled", False))


async def _count_faces_in(bitmap) -> int:
    """Faces in an already-decoded bitmap. The only detection in the project."""
    from winsdk.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winsdk.windows.media.faceanalysis import FaceDetector

    detector = await FaceDetector.create_async()
    # The detector accepts one pixel format; converting is required, not
    # optional, and a mismatch throws rather than returning nothing.
    gray = SoftwareBitmap.convert(bitmap, BitmapPixelFormat.GRAY8)
    faces = await detector.detect_faces_async(gray)
    return len(list(faces))


async def _capture_and_count() -> int:
    """Open the camera, take one frame, count faces, release it."""
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.media.capture import (MediaCapture,
                                              MediaCaptureInitializationSettings,
                                              StreamingCaptureMode)
    from winsdk.windows.media.mediaproperties import ImageEncodingProperties
    from winsdk.windows.storage.streams import InMemoryRandomAccessStream

    capture = MediaCapture()
    settings = MediaCaptureInitializationSettings()
    settings.streaming_capture_mode = StreamingCaptureMode.VIDEO
    await capture.initialize_async(settings)
    try:
        stream = InMemoryRandomAccessStream()
        await capture.capture_photo_to_stream_async(
            ImageEncodingProperties.create_bmp(), stream)
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        return await _count_faces_in(bitmap)
    finally:
        # Always, on every path. A camera left open is a light left on, and
        # it would block every other application that wants it.
        try:
            await capture.close_async()
        except Exception:
            log.debug("could not close the camera cleanly", exc_info=True)


def looks_present(cfg) -> bool | None:
    """True if someone is in front of the machine, None if it cannot say."""
    global _last_check

    if not enabled(cfg):
        return None
    if time.time() - _last_check < MIN_SECONDS_BETWEEN:
        return None
    _last_check = time.time()

    try:
        import asyncio
        import concurrent.futures

        # WinRT is apartment-bound; its own thread every time. The same lesson
        # OCR, the media session and the location service each taught.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            faces = pool.submit(
                lambda: asyncio.run(_capture_and_count())).result(timeout=25)
    except Exception as e:
        log.info("camera check unavailable (%s); using the keyboard alone",
                 type(e).__name__)
        return None

    # Logged every single time, deliberately. If the camera is ever used there
    # should be a record of it that does not depend on anyone remembering.
    log.info("camera check: %s", "someone is there" if faces else "nobody visible")
    return faces > 0
