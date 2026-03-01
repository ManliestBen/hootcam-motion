"""
Frame source from RTSP/MJPEG streams (e.g. from Hootcam Streamer on the Pi).
One reader per camera; each runs in a thread and updates a shared latest frame.
Uses retry with exponential backoff when connection fails or stream ends prematurely.
"""

from __future__ import annotations

import io
import logging
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Backoff when connection fails or stream drops: start at 5s, max 60s, multiply by 1.5 each failure.
RETRY_DELAY_INITIAL = 5.0
RETRY_DELAY_MAX = 60.0
RETRY_DELAY_MULTIPLIER = 1.5
# After stream ends (read failed), wait at least this long before reconnecting.
RECONNECT_AFTER_STREAM_END = 3.0

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
    Opens a stream URL (RTSP or MJPEG HTTP) and keeps the latest frame in memory.
    Call start() to begin the reader thread; stop() to shut down.
    Retries with exponential backoff on connection refused or stream end.
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
            logger.warning("OpenCV not available; stream source disabled")
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Stream reader started for camera %d: %s", self.camera_index, self.url)

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
        retry_delay = RETRY_DELAY_INITIAL
        while self._running and _CV2_AVAILABLE:
            cap = None
            try:
                cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
                if not cap.isOpened():
                    self._last_error = "Failed to open stream"
                    self._connected = False
                    logger.warning(
                        "Camera %d: stream connection failed (connection refused or unreachable). Retrying in %.0f s: %s",
                        self.camera_index, retry_delay, self.url,
                    )
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * RETRY_DELAY_MULTIPLIER, RETRY_DELAY_MAX)
                    continue
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                self._connected = True
                self._last_error = None
                retry_delay = RETRY_DELAY_INITIAL  # reset backoff after successful open
                frame_count = 0
                while self._running and cap.isOpened():
                    ret, frame = cap.read()
                    if not ret or frame is None:
                        # Stream ended prematurely (e.g. "Stream ends prematurely", server restart)
                        if frame_count > 0:
                            logger.info(
                                "Camera %d: stream ended unexpectedly, reconnecting in %.0f s: %s",
                                self.camera_index, RECONNECT_AFTER_STREAM_END, self.url,
                            )
                        time.sleep(RECONNECT_AFTER_STREAM_END)
                        retry_delay = min(retry_delay * RETRY_DELAY_MULTIPLIER, RETRY_DELAY_MAX)
                        break
                    frame_count += 1
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
                logger.warning("Camera %d: stream read error: %s. Retrying in %.0f s.", self.camera_index, e, retry_delay)
                self._last_error = str(e)
                self._connected = False
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * RETRY_DELAY_MULTIPLIER, RETRY_DELAY_MAX)
            finally:
                if cap is not None:
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
