"""
Motion-triggered recording: pre_capture buffer, event handling, and writing
pictures/movies to target_dir (SSD). Logs to SQLite via database module.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, List, Optional

from . import database
from .api.schemas import CameraConfig
from .time_util import now_central

logger = logging.getLogger(__name__)


def _str_val(x: Any) -> str:
    """Return string value from enum or str (config loaded from JSON may be str)."""
    if x is None:
        return ""
    return getattr(x, "value", x) if not isinstance(x, str) else x


def _expand_filename(
    template: str,
    event_id: int,
    camera_id: Optional[int],
    camera_name: Optional[str],
    frame_number: int = 0,
    now: Optional[datetime] = None,
) -> str:
    """Replace conversion specifiers. Minimal set: %v, %Y%m%d%H%M%S, %q, %t, %$. Uses US Central time."""
    if now is None:
        now = now_central()
    s = template
    s = s.replace("%v", str(event_id))
    s = s.replace("%Y", now.strftime("%Y"))
    s = s.replace("%m", now.strftime("%m"))
    s = s.replace("%d", now.strftime("%d"))
    s = s.replace("%H", now.strftime("%H"))
    s = s.replace("%M", now.strftime("%M"))
    s = s.replace("%S", now.strftime("%S"))
    s = s.replace("%q", str(frame_number))
    s = s.replace("%t", str(camera_id or ""))
    s = s.replace("%$", (camera_name or "").replace(" ", "_"))
    return s


class RecordingSession:
    """
    One event: pre_capture buffer, then record frames until event_gap.
    Writes pictures and/or movie to target_dir and logs to DB.
    """

    def __init__(
        self,
        camera_index: int,
        config: CameraConfig,
        target_dir: Path,
        db_path: Path,
        event_id: int,
        on_movie_end_script: Optional[str] = None,
        on_picture_save_script: Optional[str] = None,
    ) -> None:
        self.camera_index = camera_index
        self.config = config
        self.target_dir = Path(target_dir)
        self.db_path = db_path
        self.event_id = event_id
        self.on_movie_end_script = on_movie_end_script
        self.on_picture_save_script = on_picture_save_script
        self._frame_buffer: List[tuple[bytes, datetime]] = []
        self._pre_capture_filled = False
        self._frame_count = 0
        self._movie_path: Optional[Path] = None
        self._movie_frames: List[tuple[bytes, datetime]] = []  # (jpeg, timestamp) for actual FPS
        self._started_at: Optional[datetime] = None

    def add_pre_capture_frame(self, jpeg_bytes: bytes, ts: datetime) -> None:
        """Add frame to pre-capture buffer (max pre_capture frames)."""
        pre = self.config.pre_capture or 0
        if pre <= 0:
            return
        self._frame_buffer.append((jpeg_bytes, ts))
        if len(self._frame_buffer) > pre:
            self._frame_buffer.pop(0)
        self._pre_capture_filled = len(self._frame_buffer) >= pre or pre == 0

    def start_event(self, started_at: datetime) -> None:
        """Called when motion is first detected; flush pre_capture into recording."""
        self._started_at = started_at
        # Pre-capture frames go first
        for jpeg_bytes, ts in self._frame_buffer:
            self._record_frame(jpeg_bytes, ts)
        self._frame_buffer.clear()

    def record_frame(self, jpeg_bytes: bytes, ts: Optional[datetime] = None) -> None:
        """Append one frame to current event (picture and/or movie)."""
        if ts is None:
            ts = now_central()
        self._record_frame(jpeg_bytes, ts)

    def _record_frame(self, jpeg_bytes: bytes, ts: datetime) -> None:
        self._frame_count += 1
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")

        if self.config.picture_output and _str_val(self.config.picture_output) != "off":
            pic_name = _expand_filename(
                self.config.picture_filename or "%v-%Y%m%d%H%M%S-%q",
                self.event_id,
                self.config.camera_id,
                self.config.camera_name,
                self._frame_count,
                now=ts,
            )
            pt = _str_val(self.config.picture_type)
            ext = ".jpg" if pt == "jpeg" else ".webp" if pt == "webp" else ".jpg"
            pic_path = self.target_dir / f"{pic_name}{ext}"
            try:
                pic_path.write_bytes(jpeg_bytes)
                try:
                    rel = str(pic_path.relative_to(self.target_dir))
                except ValueError:
                    rel = str(pic_path)
                if self.config.sql_log_picture:
                    database.log_file(
                        self.db_path,
                        self.event_id,
                        self.camera_index,
                        "picture",
                        rel,
                        ts_str,
                        self._frame_count,
                    )
                if self.on_picture_save_script:
                    self._run_script(self.on_picture_save_script, str(pic_path))
            except Exception as e:
                logger.warning("Save picture failed: %s", e)

        if self.config.movie_output:
            self._movie_frames.append((jpeg_bytes, ts))
            # Cap to avoid unbounded memory during very long events (~15 min at 15 fps)
            max_frames = 15000
            while len(self._movie_frames) > max_frames:
                self._movie_frames.pop(0)

    def end_event(self, ended_at: datetime) -> None:
        """Flush movie to file and run on_movie_end if set."""
        if not self._movie_frames and not self._movie_path:
            if self.on_movie_end_script:
                self._run_script(self.on_movie_end_script, "")
            return

        if self.config.movie_output and self._movie_frames:
            codec = _str_val(self.config.movie_codec) or "mkv"
            ext = ".mkv" if codec == "mkv" else ".mp4" if codec in ("mp4", "hevc") else ".avi"
            name = _expand_filename(
                self.config.movie_filename or "%v-%Y%m%d%H%M%S",
                self.event_id,
                self.config.camera_id,
                self.config.camera_name,
                now=ended_at,
            )
            out_path = self.target_dir / f"{name}{ext}"
            try:
                self._encode_movie(out_path, self._movie_frames)
                try:
                    rel = str(out_path.relative_to(self.target_dir))
                except ValueError:
                    rel = str(out_path)
                if self.config.sql_log_movie:
                    database.log_file(
                        self.db_path,
                        self.event_id,
                        self.camera_index,
                        "movie",
                        rel,
                        ended_at.strftime("%Y-%m-%d %H:%M:%S"),
                        None,
                    )
                if self.on_movie_end_script:
                    self._run_script(self.on_movie_end_script, str(out_path))
            except Exception as e:
                logger.warning("Encode movie failed: %s", e)
            finally:
                self._movie_frames.clear()

    def _encode_movie(self, out_path: Path, frames_with_ts: List[tuple[bytes, datetime]]) -> None:
        """Encode buffered JPEG frames to movie via ffmpeg (image2pipe). Uses actual elapsed time for FPS so playback speed is correct at any resolution."""
        if not frames_with_ts:
            return
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Use actual time span so playback speed is correct when capture can't keep up at high res
        first_ts = frames_with_ts[0][1]
        last_ts = frames_with_ts[-1][1]
        duration_sec = (last_ts - first_ts).total_seconds()
        if duration_sec > 0 and len(frames_with_ts) > 1:
            # N frames over duration_sec → fps = N/duration so output length matches real time
            fps = len(frames_with_ts) / duration_sec
            fps = max(1.0, min(120.0, fps))  # clamp to sane range
        else:
            fps = float(self.config.framerate or 15)
        cmd = [
            "ffmpeg", "-y",
            "-f", "image2pipe",
            "-framerate", str(fps),
            "-i", "pipe:0",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(out_path),
        ]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        assert proc.stdin is not None
        for jpeg, _ in frames_with_ts:
            proc.stdin.write(jpeg)
        proc.stdin.close()
        err = proc.stderr.read()
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {err.decode()[:500]}")

    def _run_script(self, script_path: str, file_path: str) -> None:
        try:
            subprocess.Popen(
                [script_path, file_path],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.warning("Script %s failed: %s", script_path, e)


def run_script_sync(script_path: str, *args: str) -> None:
    """Run on_event_start / on_event_end / on_motion_detected (optional)."""
    try:
        subprocess.run(
            [script_path, *args],
            timeout=30,
            capture_output=True,
        )
    except Exception as e:
        logger.warning("Script %s failed: %s", script_path, e)
