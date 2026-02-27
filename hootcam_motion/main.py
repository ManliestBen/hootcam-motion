"""
Hootcam Motion: FastAPI app for motion detection and recording from RTSP streams.

Runs on the NUC; consumes RTSP streams (e.g. from Hootcam Streamer on the Pi).
No direct camera access—all video comes from configurable stream_url per camera.

Run with: uvicorn hootcam_motion.main:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from . import auth
from . import database
from . import recording
from .api.routes import router
from .api.schemas import CameraConfig, GlobalConfig
from .config import (
    ensure_config_dir_and_db,
    get_bootstrap_target_dir,
    load_camera_config,
    load_global_config,
    save_camera_config,
    save_global_config,
)
from .motion import MotionDetector
from .rtsp_source import RTSPFrameSource

# Global app state (set in lifespan)
app_state: dict[str, Any] = {}


def _setup_logging(level: int) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


async def _ingest_loop(
    camera_index: int,
    frame_source: RTSPFrameSource,
    motion_detector: MotionDetector,
    config: CameraConfig,
    global_config: GlobalConfig,
    target_dir: Path,
    db_path: Path,
    state: dict,
) -> None:
    """One loop per camera: read from RTSP -> motion -> record; update latest_jpeg."""
    import io

    log = logging.getLogger(__name__)
    event_gap_sec = config.event_gap if config.event_gap is not None and config.event_gap >= 0 else 60
    post_capture = config.post_capture or 0
    pre_capture = config.pre_capture or 0
    pre_buffer: list[tuple[bytes, datetime]] = []
    recording_session: Optional[recording.RecordingSession] = None
    last_motion_at: Optional[datetime] = None
    post_frames_left = 0
    framerate = config.framerate or 15
    interval = 1.0 / framerate
    no_frame_count = 0
    NO_FRAME_FAILURE_THRESHOLD = 30  # mark failed after this many intervals with no frame

    while True:
        try:
            if state.get("camera_failed") and state["camera_failed"][camera_index]:
                await asyncio.sleep(interval)
                continue

            frame = frame_source.get_latest_frame()
            jpeg_bytes = frame_source.get_latest_jpeg()

            if frame is None or jpeg_bytes is None:
                no_frame_count += 1
                if no_frame_count >= NO_FRAME_FAILURE_THRESHOLD and state.get("camera_failed") is not None:
                    state["camera_failed"][camera_index] = True
                    log.warning("Camera %d: no frame from RTSP for too long; marking failed.", camera_index)
                await asyncio.sleep(interval)
                continue
            no_frame_count = 0
            if state.get("camera_failed") is not None:
                state["camera_failed"][camera_index] = False

            if state.get("latest_jpeg") is not None and camera_index < len(state["latest_jpeg"]):
                state["latest_jpeg"][camera_index] = jpeg_bytes

            if state["detection_paused"][camera_index]:
                await asyncio.sleep(interval)
                continue

            motion_detected, _ = motion_detector.update(frame)
            now = datetime.utcnow()

            # On-demand snapshot
            requests = state.get("snapshot_requests") or {}
            if requests.get(camera_index) and requests[camera_index] and jpeg_bytes:
                requests[camera_index].pop()
                try:
                    name = recording._expand_filename(
                        config.snapshot_filename or "%v-%Y%m%d%H%M%S-snapshot",
                        0,
                        config.camera_id,
                        config.camera_name,
                    )
                    snap_path = target_dir / f"{name}.jpg"
                    snap_path.parent.mkdir(parents=True, exist_ok=True)
                    snap_path.write_bytes(jpeg_bytes)
                    try:
                        rel = str(snap_path.relative_to(target_dir))
                    except ValueError:
                        rel = str(snap_path)
                    if config.sql_log_snapshot:
                        database.log_file(
                            db_path,
                            None,
                            camera_index,
                            "snapshot",
                            rel,
                            now.strftime("%Y-%m-%d %H:%M:%S"),
                            None,
                        )
                except Exception as e:
                    log.warning("Snapshot save failed: %s", e)

            if recording_session is not None:
                if motion_detected:
                    last_motion_at = now
                    post_frames_left = post_capture
                    recording_session.record_frame(jpeg_bytes, now)
                elif post_frames_left > 0:
                    post_frames_left -= 1
                    recording_session.record_frame(jpeg_bytes, now)
                else:
                    if last_motion_at and (now - last_motion_at).total_seconds() >= event_gap_sec:
                        database.log_event_end(db_path, state["current_event_id"][camera_index], now.strftime("%Y-%m-%d %H:%M:%S"))
                        recording_session.end_event(now)
                        if config.on_event_end:
                            recording.run_script_sync(config.on_event_end)
                        state["current_event_id"][camera_index] = None
                        recording_session = None
                        last_motion_at = None

            elif motion_detected:
                started_at_str = now.strftime("%Y-%m-%d %H:%M:%S")
                event_id = database.log_event_start(db_path, camera_index, config.camera_id, started_at_str)
                state["current_event_id"][camera_index] = event_id
                recording_session = recording.RecordingSession(
                    camera_index,
                    config,
                    target_dir,
                    db_path,
                    event_id,
                    on_movie_end_script=config.on_movie_end,
                    on_picture_save_script=config.on_picture_save,
                )
                for jb, ts in pre_buffer:
                    recording_session.add_pre_capture_frame(jb, ts)
                pre_buffer.clear()
                recording_session.start_event(now)
                recording_session.record_frame(jpeg_bytes, now)
                last_motion_at = now
                post_frames_left = post_capture
                if config.on_event_start:
                    recording.run_script_sync(config.on_event_start)
                if config.on_motion_detected:
                    recording.run_script_sync(config.on_motion_detected)
            else:
                if pre_capture > 0 and jpeg_bytes:
                    pre_buffer.append((jpeg_bytes, now))
                    if len(pre_buffer) > pre_capture:
                        pre_buffer.pop(0)

            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.exception("Ingest loop %d error: %s", camera_index, e)
            await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start RTSP readers, load config, run ingest loops; on exit stop readers."""
    global app_state
    _setup_logging(logging.INFO)
    log = logging.getLogger(__name__)

    bootstrap_target = get_bootstrap_target_dir()
    db_path = ensure_config_dir_and_db(None, bootstrap_target)
    global_config = load_global_config(db_path=db_path)
    auth.ensure_default_user(db_path)
    global_config.target_dir = global_config.target_dir or str(Path.cwd() / "data")
    target_dir = Path(global_config.target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    camera_configs = [load_camera_config(0, db_path=db_path), load_camera_config(1, db_path=db_path)]
    detection_paused = [
        camera_configs[0].pause if camera_configs[0].pause is not None else True,
        camera_configs[1].pause if camera_configs[1].pause is not None else True,
    ]
    current_event_id: list[Optional[int]] = [None, None]
    latest_jpeg: list[Optional[bytes]] = [None, None]
    camera_failed = [False, False]

    frame_sources: list[Optional[RTSPFrameSource]] = [None, None]
    for i in range(2):
        url = (camera_configs[i].stream_url or "").strip()
        if url:
            src = RTSPFrameSource(url, camera_index=i)
            src.start()
            frame_sources[i] = src
            log.info("RTSP source %d: %s", i, url)
        else:
            log.warning("Camera %d: no stream_url configured; skipping.", i)

    motion_detectors = [
        MotionDetector(
            threshold=camera_configs[0].threshold or 1500,
            threshold_maximum=camera_configs[0].threshold_maximum or 0,
            noise_level=camera_configs[0].noise_level or 32,
            despeckle_filter=camera_configs[0].despeckle_filter,
            minimum_motion_frames=camera_configs[0].minimum_motion_frames or 1,
        ),
        MotionDetector(
            threshold=camera_configs[1].threshold or 1500,
            threshold_maximum=camera_configs[1].threshold_maximum or 0,
            noise_level=camera_configs[1].noise_level or 32,
            despeckle_filter=camera_configs[1].despeckle_filter,
            minimum_motion_frames=camera_configs[1].minimum_motion_frames or 1,
        ),
    ]

    def save_global(c: GlobalConfig) -> None:
        save_global_config(c, db_path=db_path)

    def save_camera(i: int, c: CameraConfig) -> None:
        save_camera_config(i, c, db_path=db_path)

    app_state.update({
        "global_config": global_config,
        "camera_configs": camera_configs,
        "db_path": db_path,
        "target_dir": target_dir,
        "frame_sources": frame_sources,
        "detection_paused": detection_paused,
        "current_event_id": current_event_id,
        "latest_jpeg": latest_jpeg,
        "camera_failed": camera_failed,
        "snapshot_requests": {},
        "save_global_config": save_global,
        "save_camera_config": save_camera,
        "base_url": "http://localhost:8080",
    })

    tasks = []
    for i in range(2):
        if frame_sources[i] is not None:
            t = asyncio.create_task(
                _ingest_loop(
                    i,
                    frame_sources[i],
                    motion_detectors[i],
                    camera_configs[i],
                    global_config,
                    target_dir,
                    db_path,
                    app_state,
                )
            )
            tasks.append(t)
    log.info("Hootcam Motion started; API at /docs")

    yield

    for t in tasks:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
    for src in frame_sources:
        if src is not None:
            src.stop()
    log.info("Hootcam Motion stopped")


app = FastAPI(
    title="Hootcam Motion",
    description="""
Motion detection and recording from RTSP streams (e.g. from Hootcam Streamer on the Pi).
Configure per-camera **stream_url** (RTSP). No direct camera access on this machine.
    """,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


class CORSReflectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        origin = request.headers.get("origin")
        if request.method == "OPTIONS":
            response = Response(status_code=200)
        else:
            response = await call_next(request)
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Expose-Headers"] = "*"
        if request.method == "OPTIONS":
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, PUT, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Accept"
            response.headers["Access-Control-Max-Age"] = "600"
        return response


app.add_middleware(CORSReflectMiddleware)
app.include_router(router)
