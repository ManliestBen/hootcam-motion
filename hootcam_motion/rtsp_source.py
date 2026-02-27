"""
Frame source from RTSP streams (e.g. from Hootcam Streamer on the Pi).
One reader per camera; each runs in a thread and updates a shared latest frame.
"""

from __future__ import annotations

import io
import logging
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    import cv2
    import numpy as np
    from PIL import Image
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False
    cv2 = None
    np = None
    Image = None


class RTSPFrameSource:
    """
    Opens an RTSP URL and keeps the latest frame in memory.
    Call start() to begin the reader thread; stop() to shut down.
    """

    def __init__(self, url: str, camera_index: int = 0) -> None:
        self.url = url
        self.camera_index = camera_index
        self._latest_frame: Optional[Any] = None  # numpy BGR or None
        self._latest_jpeg: Optional[bytes] = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._connected = False
        self._last_error: Optional[str] = None

    def start(self) -> None:
        if not _CV2_AVAILABLE:
            logger.warning("OpenCV not available; RTSP source disabled")
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("RTSP reader started for camera %d: %s", self.camera_index, self.url)

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        with self._lock:
            self._latest_frame = None
            self._latest_jpeg = None
            self._connected = False

    def _run(self) -> None:
        while self._running and _CV2_AVAILABLE:
            try:
                cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
                if not cap.isOpened():
                    self._last_error = "Failed to open RTSP URL"
                    time.sleep(5)
                    continue
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                self._connected = True
                self._last_error = None
                while self._running and cap.isOpened():
                    ret, frame = cap.read()
                    if not ret or frame is None:
                        break
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    _, jpeg = cv2.imencode(".jpg", frame)
                    if jpeg is not None:
                        jpeg_bytes = jpeg.tobytes()
                    else:
                        jpeg_bytes = _frame_to_jpeg(frame)
                    with self._lock:
                        self._latest_frame = gray
                        self._latest_jpeg = jpeg_bytes
            except Exception as e:
                logger.debug("RTSP read error camera %d: %s", self.camera_index, e)
                self._last_error = str(e)
                self._connected = False
            finally:
                try:
                    cap.release()
                except Exception:
                    pass
            if self._running:
                time.sleep(0.5)
        self._connected = False

    def get_latest_frame(self) -> Optional[Any]:
        """Return latest grayscale frame (numpy 2D) for motion detection."""
        with self._lock:
            return self._latest_frame.copy() if self._latest_frame is not None else None

    def get_latest_jpeg(self) -> Optional[bytes]:
        """Return latest frame as JPEG bytes for stream/recording (returns a copy)."""
        with self._lock:
            return bytes(self._latest_jpeg) if self._latest_jpeg is not None else None

    def connected(self) -> bool:
        return self._connected


def _frame_to_jpeg(frame: Any) -> bytes:
    """Fallback: BGR frame to JPEG via PIL if cv2.imencode fails."""
    if Image is None or np is None:
        return b""
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(rgb)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()
