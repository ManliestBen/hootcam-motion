"""
MJPEG streaming for live camera feeds.

Serves multipart/x-mixed-replace streams per camera so a frontend can
embed GET /cameras/0/stream and GET /cameras/1/stream.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Callable, Optional

logger = logging.getLogger(__name__)

BOUNDARY = "frame"


async def mjpeg_stream(
    frame_provider: Callable[[int], Optional[bytes]],
    camera_index: int,
    quality: int = 50,
    max_fps: float = 10.0,
) -> AsyncIterator[bytes]:
    """
    Yield MJPEG multipart response body chunks.
    frame_provider(camera_index) -> bytes | None (JPEG).
    """
    interval = 1.0 / max_fps if max_fps > 0 else 0.1
    while True:
        try:
            jpeg = frame_provider(camera_index)
            if jpeg:
                part = (
                    f"--{BOUNDARY}\r\n"
                    "Content-Type: image/jpeg\r\n"
                    f"Content-Length: {len(jpeg)}\r\n\r\n"
                )
                yield part.encode("ascii") + jpeg + b"\r\n"
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug("Stream frame error: %s", e)
            await asyncio.sleep(interval)
