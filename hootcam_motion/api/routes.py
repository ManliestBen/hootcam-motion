"""
REST API routes. Full option descriptions are in schemas.py and appear in OpenAPI docs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse

logger = logging.getLogger(__name__)
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .. import auth as auth_module
from .. import database
from ..config import get_auto_detected_ssd_path
from ..streaming import mjpeg_stream
from .schemas import (
    CameraConfig,
    CameraInfo,
    ConfigResponse,
    DetectionStatus,
    EventSummary,
    FileRecord,
    GlobalConfig,
    PasswordChangeBody,
    StorageResponse,
    StorageUpdate,
)

security = HTTPBasic()


def get_state() -> Any:
    from hootcam_motion.main import app_state
    return app_state


def get_current_user(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """Verify HTTP Basic credentials; return username or raise 401."""
    state = get_state()
    db_path = state["db_path"]
    if not auth_module.check_credentials(db_path, credentials.username, credentials.password):
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


router = APIRouter(dependencies=[Depends(get_current_user)])


# Auth routes use the same dependency (router-level) so current password is in Basic header

@router.patch(
    "/auth/password",
    status_code=204,
    tags=["Authentication"],
    summary="Change password",
    description="Set a new password. Send current credentials in Authorization (Basic) and new password in body. Only one user is supported.",
)
async def change_password(
    body: PasswordChangeBody,
    username: str = Depends(get_current_user),
    credentials: HTTPBasicCredentials = Depends(security),
):
    state = get_state()
    ok = auth_module.update_password(
        state["db_path"],
        credentials.password,
        body.new_password,
    )
    if not ok:
        raise HTTPException(401, "Invalid current password")


# --- Root (no auth for health/info; optional: require auth for consistency) ---

@router.get("/", tags=["root"])
async def root() -> dict:
    """API info and links to streams and docs."""
    return {
        "name": "Hootcam Server",
        "version": "0.1.0",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "cameras": "/cameras",
        "config": "/config",
        "events": "/events",
    }


# --- Config ---

def _build_restart_argv() -> list[str]:
    """Build argv to re-exec this server (uvicorn). Preserves --host/--port from current process."""
    args = [sys.executable, "-m", "uvicorn", "hootcam_motion.main:app"]
    argv = sys.argv
    i = 0
    while i < len(argv):
        if argv[i] == "--host" and i + 1 < len(argv):
            args.extend(["--host", argv[i + 1]])
            i += 2
            continue
        if argv[i] == "--port" and i + 1 < len(argv):
            args.extend(["--port", argv[i + 1]])
            i += 2
            continue
        i += 1
    if "--host" not in args:
        args.extend(["--host", os.environ.get("UVICORN_HOST", "0.0.0.0")])
    if "--port" not in args:
        args.extend(["--port", os.environ.get("UVICORN_PORT", "8080")])
    return args


@router.post(
    "/restart",
    tags=["Configuration"],
    summary="Restart server",
    description="Schedules a process re-exec so the server restarts in place (e.g. to apply resolution changes). Works when run as 'python -m uvicorn ...'. Host/port are preserved from the current command line or from UVICORN_HOST/UVICORN_PORT.",
)
async def restart_server() -> dict:
    """Return 200 and schedule process re-exec so the server comes back with new config."""
    async def _restart_after_delay() -> None:
        await asyncio.sleep(2)
        try:
            os.execv(sys.executable, _build_restart_argv())
        except Exception:
            pass
        os._exit(0)

    asyncio.create_task(_restart_after_delay())
    return {"message": "Server will restart shortly."}


@router.get(
    "/config",
    response_model=ConfigResponse,
    tags=["Configuration"],
    summary="Get full configuration",
    description="Returns global config and per-camera config.",
)
async def get_config() -> ConfigResponse:
    state = get_state()
    return ConfigResponse(
        global_config=state["global_config"],
        cameras=state["camera_configs"],
    )


@router.patch(
    "/config",
    response_model=GlobalConfig,
    tags=["Configuration"],
    summary="Update global configuration",
)
async def patch_global_config(update: GlobalConfig) -> GlobalConfig:
    state = get_state()
    current = state["global_config"]
    data = update.model_dump(exclude_none=True)
    for k, v in data.items():
        setattr(current, k, v)
    state["save_global_config"](current)
    return current


@router.get(
    "/storage",
    response_model=StorageResponse,
    tags=["Configuration"],
    summary="Get storage location and auto-detected SSD",
    description="Returns the current recording path and, if available, the auto-detected SSD mount path.",
)
async def get_storage() -> StorageResponse:
    state = get_state()
    current = state["global_config"].target_dir or str(state["target_dir"])
    ssd_path = get_auto_detected_ssd_path()
    return StorageResponse(current_path=current, auto_detected_ssd_path=ssd_path)


@router.patch(
    "/storage",
    response_model=StorageResponse,
    tags=["Configuration"],
    summary="Set storage location",
    description="Set storage to a manual path or to the auto-detected SSD. Takes effect after the next server restart.",
)
async def patch_storage(update: StorageUpdate) -> StorageResponse:
    state = get_state()
    current = state["global_config"]
    data = update.model_dump(exclude_none=True)
    path_value = data.get("path")
    use_ssd = data.get("use_auto_detected_ssd")
    if path_value:
        new_path = path_value
    elif use_ssd:
        ssd_path = get_auto_detected_ssd_path()
        if not ssd_path:
            raise HTTPException(
                status_code=400,
                detail="No SSD detected. Mount an SSD (e.g. Pi SSD HAT) and ensure it is non-rotational.",
            )
        new_path = ssd_path
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide 'path' (absolute directory) or 'use_auto_detected_ssd': true.",
        )
    path_obj = Path(new_path)
    if not path_obj.is_absolute():
        raise HTTPException(status_code=400, detail="path must be an absolute path.")
    try:
        path_obj.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"Cannot create directory: {e}") from e
    current.target_dir = new_path
    state["save_global_config"](current)
    return StorageResponse(
        current_path=new_path,
        auto_detected_ssd_path=get_auto_detected_ssd_path(),
    )


@router.get(
    "/cameras",
    response_model=list[CameraInfo],
    tags=["Cameras"],
    summary="List cameras",
)
async def list_cameras() -> list[CameraInfo]:
    state = get_state()
    base_url = state.get("base_url", "http://localhost:8080")
    infos = []
    for i, cfg in enumerate(state["camera_configs"]):
        infos.append(CameraInfo(
            id=i,
            name=cfg.camera_name,
            camera_id=cfg.camera_id,
            detection_paused=state["detection_paused"][i],
            stream_url=f"{base_url}/cameras/{i}/stream",
        ))
    return infos


@router.get(
    "/cameras/{camera_index}/config",
    response_model=CameraConfig,
    tags=["Configuration"],
    summary="Get camera configuration",
)
async def get_camera_config(camera_index: int) -> CameraConfig:
    state = get_state()
    if camera_index < 0 or camera_index >= len(state["camera_configs"]):
        raise HTTPException(404, "Camera not found")
    return state["camera_configs"][camera_index]


def _push_config_to_streamer(streamer_api_url: str, camera_configs: list) -> None:
    """Send cam0/cam1 resolution and fps to Hootcam Streamer (fire-and-forget)."""
    try:
        base = streamer_api_url.rstrip("/")
        url = f"{base}/config"
        payload = {}
        for i, cfg in enumerate(camera_configs[:2]):
            key = "cam0" if i == 0 else "cam1"
            payload[key] = {
                "width": getattr(cfg, "width", None) or 1920,
                "height": getattr(cfg, "height", None) or 1080,
                "fps": getattr(cfg, "framerate", None) or 25,
                "enabled": True,
            }
            if getattr(cfg, "autofocus", None) is not None:
                payload[key]["autofocus"] = cfg.autofocus
            if getattr(cfg, "lens_position", None) is not None:
                payload[key]["lens_position"] = cfg.lens_position
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="PATCH",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status not in (200, 204):
                logger.warning("Streamer PATCH /config returned %s", resp.status)
    except Exception as e:
        logger.warning("Could not push config to streamer at %s: %s", streamer_api_url, e)


@router.patch(
    "/cameras/{camera_index}/config",
    response_model=CameraConfig,
    tags=["Configuration"],
    summary="Update camera configuration",
    description="Partial update. Only provided fields are changed. If streamer_api_url is set, also pushes resolution/fps to Hootcam Streamer on the Pi.",
)
async def patch_camera_config(camera_index: int, update: CameraConfig) -> CameraConfig:
    state = get_state()
    if camera_index < 0 or camera_index >= len(state["camera_configs"]):
        raise HTTPException(404, "Camera not found")
    current = state["camera_configs"][camera_index]
    data = update.model_dump(exclude_none=True)
    for k, v in data.items():
        if hasattr(current, k):
            setattr(current, k, v)
    state["save_camera_config"](camera_index, current)
    # If streamer API URL is set, push cam0/cam1 config so UI changes trickle down to the Pi
    global_config = state["global_config"]
    api_url = getattr(global_config, "streamer_api_url", None) if global_config else None
    if api_url and api_url.strip():
        asyncio.create_task(asyncio.to_thread(_push_config_to_streamer, api_url.strip(), state["camera_configs"]))
    return current


@router.get(
    "/cameras/{camera_index}/resolutions",
    tags=["Configuration"],
    summary="List supported resolutions",
    description="Not used when video comes from RTSP (stream_url). Returns empty list.",
)
async def get_camera_resolutions(camera_index: int) -> list[dict]:
    state = get_state()
    if camera_index < 0 or camera_index >= len(state["camera_configs"]):
        raise HTTPException(404, "Camera not found")
    return []


# --- Detection control ---

@router.post(
    "/cameras/{camera_index}/detection/start",
    tags=["Detection"],
    summary="Start motion detection",
    description="Resume motion detection for this camera.",
)
async def detection_start(camera_index: int) -> dict:
    state = get_state()
    if camera_index < 0 or camera_index >= len(state["detection_paused"]):
        raise HTTPException(404, "Camera not found")
    state["detection_paused"][camera_index] = False
    # Persist so detection state survives server restart
    config = state["camera_configs"][camera_index]
    config.pause = False
    state["save_camera_config"](camera_index, config)
    return {"camera_index": camera_index, "paused": False}


@router.post(
    "/cameras/{camera_index}/detection/pause",
    tags=["Detection"],
    summary="Pause motion detection",
)
async def detection_pause(camera_index: int) -> dict:
    state = get_state()
    if camera_index < 0 or camera_index >= len(state["detection_paused"]):
        raise HTTPException(404, "Camera not found")
    state["detection_paused"][camera_index] = True
    # Persist so detection state survives server restart
    config = state["camera_configs"][camera_index]
    config.pause = True
    state["save_camera_config"](camera_index, config)
    return {"camera_index": camera_index, "paused": True}


@router.get(
    "/cameras/{camera_index}/detection/status",
    response_model=DetectionStatus,
    tags=["Detection"],
)
async def detection_status(camera_index: int) -> DetectionStatus:
    state = get_state()
    if camera_index < 0 or camera_index >= len(state["detection_paused"]):
        raise HTTPException(404, "Camera not found")
    return DetectionStatus(
        camera_index=camera_index,
        paused=state["detection_paused"][camera_index],
        in_event=state["current_event_id"][camera_index] is not None,
        event_id=state["current_event_id"][camera_index],
    )


@router.post(
    "/cameras/{camera_index}/action/snapshot",
    tags=["Detection"],
    summary="Take snapshot",
    description="Trigger a single snapshot.",
)
async def action_snapshot(camera_index: int) -> dict:
    state = get_state()
    if camera_index < 0 or camera_index >= len(state["camera_configs"]):
        raise HTTPException(404, "Camera not found")
    # Signal snapshot requested; capture loop will write one snapshot (cap queue to avoid unbounded growth)
    state.setdefault("snapshot_requests", {})
    lst = state["snapshot_requests"].setdefault(camera_index, [])
    if len(lst) < 10:
        lst.append(1)
    return {"camera_index": camera_index, "requested": True}


# --- Streams ---

@router.get(
    "/cameras/{camera_index}/stream",
    tags=["Streams"],
    summary="MJPEG live stream",
    description="Continuous MJPEG stream (multipart/x-mixed-replace). Use in <img src=\"...\"> or video players.",
    response_class=StreamingResponse,
)
async def camera_stream(
    camera_index: int,
) -> StreamingResponse:
    state = get_state()
    if camera_index < 0 or camera_index >= len(state["camera_configs"]):
        raise HTTPException(404, "Camera not found")
    quality = state["global_config"].stream_quality or 50
    maxrate = state["global_config"].stream_maxrate or 10
    if maxrate == 100:
        maxrate = 30

    def provider(idx: int) -> Optional[bytes]:
        latest = state.get("latest_jpeg")
        if latest and idx < len(latest):
            return latest[idx]
        return None

    return StreamingResponse(
        mjpeg_stream(provider, camera_index, quality=quality, max_fps=float(maxrate)),
        media_type=f"multipart/x-mixed-replace; boundary=frame",
    )


@router.get(
    "/cameras/{camera_index}/current",
    tags=["Streams"],
    summary="Current frame (single JPEG)",
    response_class=Response,
)
async def camera_current(camera_index: int) -> Response:
    state = get_state()
    if camera_index < 0 or camera_index >= len(state.get("latest_jpeg", [])):
        raise HTTPException(404, "Camera not found")
    jpeg = state["latest_jpeg"][camera_index]
    if not jpeg:
        raise HTTPException(503, "No frame available")
    # Ensure bytes so FastAPI doesn't try to JSON-serialize (e.g. if it was memoryview)
    body = bytes(jpeg) if not isinstance(jpeg, bytes) else jpeg
    return Response(content=body, media_type="image/jpeg")


# --- Events & files ---

@router.get(
    "/events",
    response_model=list[EventSummary],
    tags=["Events & Files"],
    summary="List motion events",
    description="Events are stored in SQLite; media files are on disk (target_dir).",
)
async def list_events(
    camera_index: Optional[int] = Query(None, description="Filter by camera index"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[EventSummary]:
    state = get_state()
    db_path = state["db_path"]
    rows = database.get_events(db_path, camera_index, limit, offset)
    return [
        EventSummary(
            id=r["id"],
            camera_index=r["camera_index"],
            camera_id=r.get("camera_id"),
            started_at=r["started_at"],
            ended_at=r.get("ended_at"),
            file_count=r.get("file_count", 0),
        )
        for r in rows
    ]


@router.get(
    "/events/{event_id}",
    tags=["Events & Files"],
    summary="Get one event",
)
async def get_event(event_id: int) -> dict:
    state = get_state()
    row = database.get_event(state["db_path"], event_id)
    if not row:
        raise HTTPException(404, "Event not found")
    return dict(row)


@router.get(
    "/files",
    response_model=list[FileRecord],
    tags=["Events & Files"],
    summary="List file records",
    description="Records of saved pictures/movies (paths relative to target_dir).",
)
async def list_files(
    event_id: Optional[int] = Query(None),
    camera_index: Optional[int] = Query(None),
    file_type: Optional[str] = Query(None, description="picture, movie, snapshot, timelapse"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[FileRecord]:
    state = get_state()
    rows = database.get_files(
        state["db_path"],
        event_id=event_id,
        camera_index=camera_index,
        file_type=file_type,
        limit=limit,
        offset=offset,
    )
    return [
        FileRecord(
            id=r["id"],
            event_id=r.get("event_id"),
            camera_index=r["camera_index"],
            file_type=r["file_type"],
            file_path=r["file_path"],
            timestamp=r["timestamp"],
            frame_number=r.get("frame_number"),
        )
        for r in rows
    ]


def _resolve_file_path(state: dict, file_id: int, require_exists: bool = True) -> tuple[dict, Path]:
    """Resolve file_id to DB row and absolute path. Raises HTTPException if not found or invalid.
    If require_exists is False, do not require the file to exist on disk (for delete of orphan records).
    """
    row = database.get_file(state["db_path"], file_id)
    if not row:
        raise HTTPException(404, "File not found")
    target_dir = Path(state["target_dir"])
    file_path = Path(row["file_path"])
    if file_path.is_absolute():
        raise HTTPException(403, "Invalid file path")
    try:
        base = target_dir.resolve()
        full_path = (target_dir / file_path).resolve()
        if os.path.commonpath([base, full_path]) != str(base):
            raise HTTPException(403, "Invalid file path")
    except (ValueError, OSError):
        raise HTTPException(403, "Invalid file path")
    if require_exists and not full_path.is_file():
        raise HTTPException(404, "File not found on disk")
    return row, full_path


# Media type by file_type and extension
_FILE_MEDIA_TYPES = {
    "picture": "image/jpeg",
    "movie": "video/mp4",  # may be mkv, etc.; we'll refine by extension
    "snapshot": "image/jpeg",
    "timelapse": "video/mpeg",
}


def _media_type_for_path(file_path: str, file_type: str) -> str:
    path = Path(file_path)
    suf = path.suffix.lower()
    if suf in (".jpg", ".jpeg"):
        return "image/jpeg"
    if suf == ".webp":
        return "image/webp"
    if suf == ".ppm":
        return "image/x-portable-pixmap"
    if suf in (".mp4", ".mov"):
        return "video/mp4"
    if suf == ".mkv":
        return "video/x-matroska"
    if suf == ".avi":
        return "video/x-msvideo"
    return _FILE_MEDIA_TYPES.get(file_type, "application/octet-stream")


@router.get(
    "/files/{file_id}/content",
    tags=["Events & Files"],
    summary="Download picture or video file",
    description="Return the file bytes for a recorded picture or movie. Use the file id from GET /files. Safe for <img src>, <video src>, or direct download.",
    response_class=FileResponse,
)
async def get_file_content(file_id: int):
    """Serve a recorded file by its database id. Path is resolved under target_dir."""
    state = get_state()
    row, full_path = _resolve_file_path(state, file_id)
    media_type = _media_type_for_path(row["file_path"], row["file_type"])
    return FileResponse(
        path=str(full_path),
        media_type=media_type,
        filename=full_path.name,
    )


@router.get(
    "/files/{file_id}/thumbnail",
    tags=["Events & Files"],
    summary="Thumbnail (first frame) for a video file",
    description="Returns the first frame of a movie/timelapse as JPEG. Use in list views; load full video on click. 404 for non-video types.",
    response_class=Response,
)
async def get_file_thumbnail(file_id: int):
    """Return first frame of video as JPEG for list/thumbnail display. Only for movie/timelapse."""
    state = get_state()
    row, full_path = _resolve_file_path(state, file_id)
    file_type = row.get("file_type") or ""
    if file_type not in ("movie", "timelapse"):
        raise HTTPException(404, "Thumbnail only for video files")
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-i", str(full_path),
                "-vframes", "1",
                "-q:v", "2",
                "-f", "image2",
                "pipe:1",
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0 or not result.stdout:
            logger.debug("ffmpeg thumbnail failed for %s: %s", full_path, result.stderr[:200] if result.stderr else "")
            raise HTTPException(502, "Could not generate thumbnail")
        return Response(content=result.stdout, media_type="image/jpeg")
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Thumbnail generation timed out")
    except FileNotFoundError:
        raise HTTPException(503, "ffmpeg not available")


@router.delete(
    "/files/{file_id}",
    status_code=204,
    tags=["Events & Files"],
    summary="Delete a file",
    description="Deletes the file from disk (if present) and removes its record from the database. Succeeds even when the file is already missing (e.g. orphan record).",
)
async def delete_file_route(file_id: int):
    """Delete file from disk (if it exists) and database. Allows deleting records whose files are already missing."""
    state = get_state()
    row, full_path = _resolve_file_path(state, file_id, require_exists=False)
    try:
        if full_path.is_file():
            full_path.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("Could not delete file %s: %s", full_path, e)
    deleted = database.delete_file(state["db_path"], file_id)
    if not deleted:
        raise HTTPException(404, "File not found")


@router.get(
    "/cameras/{camera_index}/status",
    tags=["Cameras"],
    summary="Camera status (connection)",
)
async def camera_status(camera_index: int) -> dict:
    state = get_state()
    sources = state.get("frame_sources") or []
    if camera_index < 0 or camera_index >= len(sources):
        return {"camera_index": camera_index, "connected": False}
    src = sources[camera_index]
    return {
        "camera_index": camera_index,
        "connected": src.connected() if src is not None else False,
    }
