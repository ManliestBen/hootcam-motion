"""
Pydantic schemas for API request/response and configuration.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# --- Enums for discrete options ---


class PictureOutputType(str, Enum):
    """picture_output: Controls output of normal image. 'first' = first motion pic per event; 'best' = pic with most changed pixels."""

    on = "on"
    off = "off"
    first = "first"
    best = "best"


class LocateMotionMode(str, Enum):
    """locate_motion_mode: Draw box around moving object. 'preview' = only on preview jpeg, not on movie."""

    on = "on"
    off = "off"
    preview = "preview"


class LocateMotionStyle(str, Enum):
    """locate_motion_style: Style of the locate box."""

    box = "box"
    redbox = "redbox"
    cross = "cross"
    redcross = "redcross"


class FlipAxis(str, Enum):
    """flip_axis: Flip image (mirror)."""

    none = "none"
    v = "v"
    h = "h"


class MovieCodec(str, Enum):
    """movie_codec: Container/codec for motion-triggered movies."""

    mpeg4 = "mpeg4"
    msmpeg4 = "msmpeg4"
    swf = "swf"
    flv = "flv"
    ffv1 = "ffv1"
    mov = "mov"
    mp4 = "mp4"
    mkv = "mkv"
    hevc = "hevc"


class PictureType(str, Enum):
    """picture_type: Type of picture file to output."""

    jpeg = "jpeg"
    webp = "webp"
    ppm = "ppm"
    grey = "grey"


# --- Global configuration (applies to all cameras) ---


class GlobalConfig(BaseModel):
    """Global configuration. Applies to all cameras unless overridden in camera config."""

    # target_dir: Full path for picture and movie files. Default is current directory (SD card).
    target_dir: Optional[str] = Field(
        default=None,
        description="Full path for the target directory for picture and movie files. "
        "Default is the current working directory (SD card). Use /storage or PATCH /storage to set SSD.",
        max_length=4095,
    )

    # log_level: 1-9 (EMR, ALR, CRT, ERR, WRN, NTC, INF, DBG, ALL). Default 6.
    log_level: Optional[int] = Field(
        default=6,
        ge=1,
        le=9,
        description="Verbosity of log messages. 1=minimal, 9=all. Use INF (7) when reporting issues.",
    )

    # log_file: Path for log file. If not set, stderr/syslog.
    log_file: Optional[str] = Field(
        default=None,
        description="Full path and filename for logging. If not defined, stderr/syslog is used.",
        max_length=4095,
    )

    # stream_localhost: Limit stream to localhost only.
    stream_localhost: Optional[bool] = Field(
        default=True,
        description="If on, stream can only be accessed from the same machine.",
    )

    # stream_quality: JPEG quality (1-100) for live stream.
    stream_quality: Optional[int] = Field(
        default=50,
        ge=1,
        le=100,
        description="Quality in percent for JPEG frames on the live stream. Lower = less bandwidth.",
    )

    # stream_maxrate: Max framerate (fps) for stream. 100 = practically unlimited.
    stream_maxrate: Optional[int] = Field(
        default=15,
        ge=1,
        le=100,
        description="Limit stream framerate in fps. 100 = unlimited.",
    )

    # stream_grey: Send stream in greyscale to save bandwidth.
    stream_grey: Optional[bool] = Field(
        default=False,
        description="Send live stream in grey (black and white) rather than color.",
    )

    # stream_motion: Limit stream to 1 fps when no motion, full rate when motion.
    stream_motion: Optional[bool] = Field(
        default=False,
        description="When on, stream is 1 fps with no motion and stream_maxrate when motion detected.",
    )

    # stream_failure_sec: Seconds with no frame before marking camera as failed. Default 15.
    stream_failure_sec: Optional[int] = Field(
        default=15,
        ge=3,
        le=300,
        description="Seconds with no frame from stream before marking camera failed. Higher = more tolerant of brief dropouts.",
    )

    # stream_retry_sec: When failed, re-attempt reading every this many seconds. Default 5.
    stream_retry_sec: Optional[int] = Field(
        default=5,
        ge=2,
        le=60,
        description="When a camera is marked failed, clear failed and try reading again every this many seconds (reconnection).",
    )

    # database_busy_timeout: SQLite only. Max ms to wait for locked table.
    database_busy_timeout: Optional[int] = Field(
        default=0,
        ge=0,
        description="SQLite: max milliseconds to wait for locked table before abandoning SQL statement. 0 = immediate.",
    )


# --- Per-camera configuration ---


class CameraConfig(BaseModel):
    """Per-camera configuration."""

    # camera_name: Name for format specifiers and UI.
    camera_name: Optional[str] = Field(
        default=None,
        description="Camera name for filenames and interface.",
        max_length=4095,
    )

    # camera_id: Numeric id (1-32000), unique per camera.
    camera_id: Optional[int] = Field(
        default=None,
        ge=1,
        le=32000,
        description="Numeric id for database, format specifiers, and streams. Must be unique.",
    )

    # stream_url: Video stream URL for this camera (e.g. from Hootcam Streamer on the Pi). Required for motion/recording.
    stream_url: Optional[str] = Field(
        default=None,
        description="Stream URL: MJPEG (e.g. http://pi-ip:8080/stream) or RTSP (e.g. rtsp://pi-ip:8554/cam0). Required to receive video for this camera.",
        max_length=2048,
    )

    # --- Image capture (used for motion/display; source is stream_url) ---
    # width, height: For scaling/display only when different from stream.
    # framerate: Used for capture loop interval and recording timing.
    width: Optional[int] = Field(
        default=640,
        ge=8,
        description="Width in pixels of each frame. Device-dependent; multiple of 8.",
    )
    height: Optional[int] = Field(
        default=480,
        ge=8,
        description="Height in pixels of each frame. Device-dependent; multiple of 8.",
    )

    # framerate: Max frames per second (2-100). Default 15.
    framerate: Optional[int] = Field(
        default=15,
        ge=2,
        le=100,
        description="Maximum frames to capture per second. Higher = more CPU and more frames in recordings.",
    )

    # minimum_frame_time: Min seconds between frames. 0 = use framerate.
    minimum_frame_time: Optional[int] = Field(
        default=0,
        ge=0,
        description="Minimum seconds between frames. 0 = disabled (use framerate). Use for sub-2fps capture.",
    )

    # rotate: 0, 90, 180, 270.
    rotate: Optional[Literal[0, 90, 180, 270]] = Field(
        default=0,
        description="Rotate image degrees. 180 for upside-down camera. Width/height swap at 90/270.",
    )

    # flip_axis: none, v, h.
    flip_axis: Optional[FlipAxis] = Field(
        default=FlipAxis.none,
        description="Flip image on axis (e.g. mirror).",
    )

    # --- Motion detection ---
    # threshold: Number of changed pixels to declare motion. Key setting.
    threshold: Optional[int] = Field(
        default=1500,
        ge=1,
        description="Number of changed pixels (after noise/despeckle) to trigger motion. Lower = more sensitive.",
    )

    # threshold_maximum: Max changed pixels to trigger; above = no event. 0 = off.
    threshold_maximum: Optional[int] = Field(
        default=0,
        ge=0,
        description="Max changed pixels that trigger motion; above this no event. 0 = disabled.",
    )

    # threshold_tune: Auto-tune threshold. Manual threshold ignored when on.
    threshold_tune: Optional[bool] = Field(
        default=False,
        description="Automatically tune threshold. threshold option ignored when on.",
    )

    # noise_level: Pixel-level change threshold to count as motion (1-255). Default 32.
    noise_level: Optional[int] = Field(
        default=32,
        ge=1,
        le=255,
        description="Pixel intensity must change more than +/- this to be counted. Reduces camera noise.",
    )

    # noise_tune: Auto-tune noise level.
    noise_tune: Optional[bool] = Field(
        default=True,
        description="Automatically tune noise level.",
    )

    # despeckle_filter: E/e (erode), D/d (dilate), trailing 'l' = labeling. EedDl is common.
    despeckle_filter: Optional[str] = Field(
        default=None,
        description="Despeckle: E,e,D,d and optional 'l'. EedDl is a good start. 'l' must be last.",
        max_length=32,
    )

    # minimum_motion_frames: Frames in a row with motion before event. 1-1000s.
    minimum_motion_frames: Optional[int] = Field(
        default=1,
        ge=1,
        description="Frames that must contain motion in a row to count as true motion. 1-5 recommended.",
    )

    # event_gap: Seconds of no motion to end event. -1 = disable events (single movie).
    event_gap: Optional[int] = Field(
        default=60,
        ge=-1,
        description="Seconds with no motion that end an event. -1 = no events (one continuous movie, no pre_capture).",
    )

    # pre_capture: Frames to include from before motion (buffered). 0-100.
    pre_capture: Optional[int] = Field(
        default=0,
        ge=0,
        le=100,
        description="Number of pre-captured (buffered) frames before motion to include. 0-5 typical.",
    )

    # post_capture: Frames to capture after motion. Preferred for smooth clips.
    post_capture: Optional[int] = Field(
        default=0,
        ge=0,
        description="Frames to capture after motion. Use for smooth videos (e.g. framerate * 5 for 5 sec).",
    )

    # pause: Motion detection paused (persisted; default True = not detecting until user starts).
    pause: Optional[bool] = Field(
        default=True,
        description="When true, motion detection is paused (no recording on motion). Persists across restarts. Use detection/start to resume.",
    )

    # emulate_motion: Always save (no motion required).
    emulate_motion: Optional[bool] = Field(
        default=False,
        description="Always save images/movies even when no motion detected.",
    )

    # --- Pictures ---
    # picture_output: on, off, first, best.
    picture_output: Optional[PictureOutputType] = Field(
        default=PictureOutputType.off,
        description="Save normal stills on motion. 'first' or 'best' = one per event.",
    )

    # picture_output_motion: Save debug motion (changed pixels) images.
    picture_output_motion: Optional[bool] = Field(
        default=False,
        description="Save motion-type (changed pixels) images for tuning.",
    )

    # picture_type: jpeg, webp, ppm, grey.
    picture_type: Optional[PictureType] = Field(
        default=PictureType.jpeg,
        description="Format for saved pictures.",
    )

    # picture_quality: 1-100 for jpeg/webp.
    picture_quality: Optional[int] = Field(
        default=75,
        ge=1,
        le=100,
        description="JPEG/WebP quality percent. 100 = minimal compression.",
    )

    # picture_filename: Format string. Conversion specifiers: %v=event, %Y%m%d%H%M%S, %q=frame, %t=cam id, %$=camera name.
    picture_filename: Optional[str] = Field(
        default="%v-%Y%m%d%H%M%S-%q",
        description="Filename for pictures (relative to target_dir). Use %v, %Y%m%d%H%M%S, %q, %t, %$.",
        max_length=4095,
    )

    # snapshot_interval: Seconds between snapshots. 0 = disabled.
    snapshot_interval: Optional[int] = Field(
        default=0,
        ge=0,
        description="Seconds between periodic snapshots. 0 = disabled.",
    )

    # snapshot_filename: Format for snapshot files.
    snapshot_filename: Optional[str] = Field(
        default="%v-%Y%m%d%H%M%S-snapshot",
        description="Filename for periodic snapshots (relative to target_dir). Use %v, %Y%m%d%H%M%S, etc.",
        max_length=4095,
    )

    # --- Movies ---
    # movie_output: Encode motion-triggered movies.
    movie_output: Optional[bool] = Field(
        default=False,
        description="Encode and save movies on motion (per event). Off on install; enable in camera config when ready.",
    )

    # movie_output_motion: Save motion-type (debug) movies.
    movie_output_motion: Optional[bool] = Field(
        default=False,
        description="Save motion-pixel (debug) movies.",
    )

    # movie_max_time: Max movie length in seconds. 0 = unlimited.
    movie_max_time: Optional[int] = Field(
        default=120,
        ge=0,
        description="Maximum length of one movie in seconds. 0 = unlimited.",
    )

    # movie_bps: Bitrate (bits per second). Ignored if movie_quality set.
    movie_bps: Optional[int] = Field(
        default=400000,
        ge=0,
        description="Movie bitrate (bits/sec). Ignored when movie_quality is not 0.",
    )

    # movie_quality: 0 = use movie_bps; 1-100 = variable bitrate quality.
    movie_quality: Optional[int] = Field(
        default=60,
        ge=0,
        le=100,
        description="Variable bitrate quality (1-100). 0 = use movie_bps instead.",
    )

    # movie_codec: Container/codec. mkv, mp4, hevc, etc.
    movie_codec: Optional[MovieCodec] = Field(
        default=MovieCodec.mkv,
        description="Container/codec for motion movies (e.g. mkv, mp4, hevc).",
    )

    # movie_filename: Format string for movie files.
    movie_filename: Optional[str] = Field(
        default="%v-%Y%m%d%H%M%S",
        description="Filename for movies (relative to target_dir). Extension added by codec.",
        max_length=4095,
    )

    # --- Overlays / locate ---
    # locate_motion_mode: on, off, preview.
    locate_motion_mode: Optional[LocateMotionMode] = Field(
        default=LocateMotionMode.off,
        description="Draw box around moving object. preview = only on preview jpeg.",
    )

    # locate_motion_style: box, redbox, cross, redcross.
    locate_motion_style: Optional[LocateMotionStyle] = Field(
        default=LocateMotionStyle.box,
        description="Style of motion locate box.",
    )

    # text_left, text_right: Overlay text. Conversion specifiers allowed.
    text_left: Optional[str] = Field(
        default=None,
        description="User-defined text overlaid lower-left. Use conversion specifiers (%Y, %m, %d, %T, %v, %q, %t, %$, %C) and \\n for newline.",
        max_length=4095,
    )
    text_right: Optional[str] = Field(
        default="%Y-%m-%d\\n%T",
        description="Text overlaid lower-right. Default: date and time. Use %Y-%m-%d, %T, %v, %q, %t, %$, %C.",
        max_length=4095,
    )
    text_changes: Optional[bool] = Field(
        default=False,
        description="If on, show number of changed pixels on image (for tuning).",
    )
    text_scale: Optional[int] = Field(
        default=1,
        ge=1,
        le=10,
        description="Scale for overlay text (1-10). Useful for large resolutions.",
    )
    text_event: Optional[str] = Field(
        default="%Y%m%d%H%M%S",
        description="Defines %C for filenames and text. Timestamp of first image in event.",
        max_length=4095,
    )

    # --- Script hooks: full path to executable; run when event occurs ---
    on_event_start: Optional[str] = Field(
        default=None,
        description="Full path to script/executable run at start of a motion event.",
        max_length=4095,
    )
    on_event_end: Optional[str] = Field(
        default=None,
        description="Full path to script/executable run when event ends (after event_gap).",
        max_length=4095,
    )
    on_motion_detected: Optional[str] = Field(
        default=None,
        description="Full path to script/executable run when motion is first detected.",
        max_length=4095,
    )
    on_picture_save: Optional[str] = Field(
        default=None,
        description="Full path to script/executable run when a picture is saved; filename passed as argument.",
        max_length=4095,
    )
    on_movie_start: Optional[str] = Field(
        default=None,
        description="Full path to script/executable run when movie recording starts.",
        max_length=4095,
    )
    on_movie_end: Optional[str] = Field(
        default=None,
        description="Full path to script/executable run when movie file is closed; filename passed as argument.",
        max_length=4095,
    )

    # --- SQLite logging (we use SQLite only) ---
    sql_log_picture: Optional[bool] = Field(default=False, description="Log picture saves to database.")
    sql_log_movie: Optional[bool] = Field(default=True, description="Log movie file creation to database.")
    sql_log_snapshot: Optional[bool] = Field(default=True, description="Log snapshots to database.")

    class Config:
        extra = "ignore"  # ignore unknown keys on PATCH


# --- API response / list schemas ---


class CameraInfo(BaseModel):
    """Minimal camera info for listing."""

    id: int
    name: Optional[str] = None
    camera_id: Optional[int] = None
    detection_paused: bool = False
    stream_url: str = ""


class EventSummary(BaseModel):
    """Event record from database (metadata only; media on disk)."""

    id: int
    camera_index: int
    camera_id: Optional[int]
    started_at: str
    ended_at: Optional[str]
    file_count: int = 0


class FileRecord(BaseModel):
    """File record (picture or movie) from database."""

    id: int
    event_id: Optional[int]
    camera_index: int
    file_type: str  # 'picture', 'movie', 'snapshot', 'timelapse'
    file_path: str
    timestamp: str
    frame_number: Optional[int] = None


class ConfigResponse(BaseModel):
    """Full config: global + per-camera."""

    global_config: GlobalConfig
    cameras: list[CameraConfig]


class StorageResponse(BaseModel):
    """Current storage path and optional auto-detected SSD path."""

    current_path: str = Field(description="Absolute path where recordings are stored (SD or SSD).")
    auto_detected_ssd_path: Optional[str] = Field(
        default=None,
        description="If an SSD is attached and detected, its suggested mount path (or subdir). None otherwise.",
    )


class StorageUpdate(BaseModel):
    """Set storage location: provide a path and/or request auto-detected SSD."""

    path: Optional[str] = Field(
        default=None,
        max_length=4095,
        description="Set storage to this absolute path. Omit if using use_auto_detected_ssd.",
    )
    use_auto_detected_ssd: Optional[bool] = Field(
        default=None,
        description="If true, set storage to the auto-detected SSD mount path. Ignored if path is also set.",
    )


class DetectionStatus(BaseModel):
    """Detection status for one camera."""

    camera_index: int
    paused: bool
    in_event: bool
    event_id: Optional[int] = None


class PasswordChangeBody(BaseModel):
    """Body for PATCH /auth/password."""

    new_password: str
