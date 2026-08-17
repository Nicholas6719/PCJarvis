"""The camera check, tested without ever opening the camera.

Everything here runs against a synthetic image or a stub. _capture_and_count is
never called, so no frame is taken from the real device and the light never
comes on. That is deliberate: the first genuine capture on this machine should
be one its owner asked for, not one a test suite took while nobody was looking.

What that leaves untested is exactly one thing -- whether the detector finds a
real face through the real lens. Everything around it is covered: that it is
off by default, that being off means no camera code runs at all, that it is
rate limited, that any failure leaves the keyboard in charge, and that the
detector itself works on a bitmap.
"""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from jarvis import camera, presence  # noqa: E402

passed = 0
failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}" + (f"   {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f"   {detail}" if detail else ""))


class Cfg:
    def __init__(self, **over):
        self.values = {"presence.enabled": True, "presence.away_after_s": 300,
                       "camera.enabled": False}
        self.values.update(over)

    def get(self, key, default=None):
        return self.values.get(key, default)


print("\n[default] it is off, and off means nothing runs")
check("disabled unless switched on", camera.enabled(Cfg()) is False)
check("and disabled in the real config",
      camera.enabled(__import__("jarvis.config", fromlist=["CONFIG"]).CONFIG) is False,
      "config.yaml ships with camera.enabled: false")

# If anything were to reach the device while disabled, this would raise.
def _must_not_run():
    raise AssertionError("the camera was opened while disabled")


real_capture = camera._capture_and_count
camera._capture_and_count = _must_not_run
try:
    check("no camera code runs when disabled",
          camera.looks_present(Cfg()) is None)
finally:
    camera._capture_and_count = real_capture


print("\n[rate limit] rare by construction, not by good intentions")
camera._last_check = 0.0
calls = {"n": 0}


async def _fake():
    calls["n"] += 1
    return 1


camera._capture_and_count = _fake
try:
    first = camera.looks_present(Cfg(**{"camera.enabled": True}))
    second = camera.looks_present(Cfg(**{"camera.enabled": True}))
    check("the first check happens", first is True, str(first))
    check("the second is refused", second is None, str(second))
    check("and the device was touched once", calls["n"] == 1, str(calls["n"]))
finally:
    camera._capture_and_count = real_capture


print("\n[failure] a broken camera must not change the answer")
camera._last_check = 0.0


async def _boom():
    raise RuntimeError("no camera attached")


camera._capture_and_count = _boom
try:
    check("an error means no opinion, not a guess",
          camera.looks_present(Cfg(**{"camera.enabled": True})) is None)
finally:
    camera._capture_and_count = real_capture


print("\n[detector] the face detector itself works, on a synthetic image")


async def _count_on_blank() -> int:
    from PIL import Image
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.storage.streams import (DataWriter,
                                                InMemoryRandomAccessStream)

    image = Image.new("RGB", (640, 480), (30, 30, 30))
    buffer = io.BytesIO()
    image.save(buffer, format="BMP")

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream.get_output_stream_at(0))
    writer.write_bytes(buffer.getvalue())
    await writer.store_async()
    await writer.flush_async()
    stream.seek(0)

    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    return await camera._count_faces_in(bitmap)


try:
    found = asyncio.run(_count_on_blank())
    check("runs end to end on a bitmap", isinstance(found, int), f"{found} faces")
    check("and finds nobody in an empty frame", found == 0, str(found))
except Exception as e:
    check("the detector runs", False, f"{type(e).__name__}: {e}")


print("\n[presence] the keyboard stays in charge")
p = presence.Presence(Cfg())
presence.idle_seconds = lambda: 900.0
p.update()
check("written off as away when the camera is off", p.present() is False)

p2 = presence.Presence(Cfg(**{"camera.enabled": True}))
p2._camera_says_present = lambda: True
p2.update()
check("kept present when the camera sees someone", p2.present() is True,
      "reading on screen is not an absence")

p3 = presence.Presence(Cfg(**{"camera.enabled": True}))
p3._camera_says_present = lambda: False
p3.update()
check("still away when the camera sees nobody", p3.present() is False)

print("\n" + "=" * 66)
print(f" {passed} passed, {failed} failed")
print(" the camera was never opened during this run")
print("=" * 66)
sys.exit(1 if failed else 0)
