"""
Configuration load/save. Config can come from: defaults, environment (HOOTCAM_*), JSON file, and SQLite.
Order of precedence: DB (if persist_config) > file > env > defaults.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

from .api.schemas import CameraConfig, GlobalConfig
from .database import config_get_json, config_set_json, get_db_path, init_db

# Number of cameras (RTSP streams; typically 2 from one Pi streamer)
NUM_CAMERAS = 2


def _block_device_is_ssd(device_name: str) -> bool:
    """Return True if the block device (e.g. nvme0n1, sda) is non-rotational (SSD)."""
    rotational_path = Path("/sys/block") / device_name / "queue/rotational"
    try:
        return rotational_path.read_text().strip() == "0"
    except (OSError, IOError):
        return False


def _base_block_device(device: str) -> Optional[str]:
    """From /dev/nvme0n1p1 or /dev/sda1 return nvme0n1 or sda. Only NVMe/sd* (skip mmcblk/loop/dm)."""
    if not device or not device.startswith("/dev/"):
        return None
    name = device.removeprefix("/dev/")
    # Strip partition suffix: nvme0n1p1 -> nvme0n1, sda1 -> sda
    if re.match(r"nvme\d+n\d+", name):
        base = re.sub(r"p\d+$", "", name)
    elif re.match(r"sd[a-z]", name):
        base = re.sub(r"\d+$", "", name) or name
    else:
        return None  # e.g. mmcblk0 (SD/eMMC), loop, dm
    if base.startswith("loop") or base.startswith("dm-"):
        return None
    return base


def _detect_ssd_mounts() -> list[Path]:
    """
    Return mount points that are on SSD (non-rotational) devices, preferring NVMe.
    Skips root, /boot, and small/system mounts. Used to choose default storage.
    """
    candidates: list[tuple[Path, bool]] = []  # (path, is_nvme)
    try:
        with open("/proc/mounts", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 2:
                    continue
                device, mountpoint = parts[0], parts[1]
                base = _base_block_device(device)
                if not base:
                    continue
                if not _block_device_is_ssd(base):
                    continue
                path = Path(mountpoint)
                if not path.is_dir():
                    continue
                # Skip root, /boot, and system
                if mountpoint in ("/", "/boot", "/boot/firmware"):
                    continue
                is_nvme = base.startswith("nvme")
                candidates.append((path, is_nvme))
    except OSError:
        pass
    # Prefer NVMe (common for Pi SSD HAT), then other SSDs
    candidates.sort(key=lambda x: (not x[1], x[0]))
    return [p for p, _ in candidates]


def _default_target_dir() -> Path:
    """
    Default storage for recordings: SD card (current working directory).
    HOOTCAM_TARGET_DIR overrides this (see load_global_config). Use the storage API
    or PATCH /config to switch to SSD (manual path or auto-detect).
    """
    return Path(os.getcwd())


def get_bootstrap_target_dir() -> str:
    """
    Return the initial target directory used at startup (env or cwd).
    Used so we can resolve the config DB path before loading config from it.
    """
    return os.environ.get("HOOTCAM_TARGET_DIR") or str(_default_target_dir())


def get_auto_detected_ssd_path() -> Optional[str]:
    """
    Return the first auto-detected SSD mount path suitable for storage (with optional
    hootcam-server subdir), or None if no SSD is detected. For use by the storage API.
    """
    for mount in _detect_ssd_mounts():
        for sub in (mount / "hootcam-server", mount):
            if sub.is_dir():
                return str(sub)
        return str(mount)
    return None


def load_global_config(
    config_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
    persist_config: bool = True,
) -> GlobalConfig:
    """Load global config from DB or file or env, then apply defaults."""
    defaults = GlobalConfig()
    data: dict[str, Any] = {}

    if db_path is None:
        db_path = get_db_path(config_dir)
    if persist_config and db_path.exists():
        raw = config_get_json(db_path, "global_config")
        if isinstance(raw, dict):
            data = raw

    # Override from env
    if os.environ.get("HOOTCAM_TARGET_DIR"):
        data["target_dir"] = os.environ["HOOTCAM_TARGET_DIR"]
    if os.environ.get("HOOTCAM_LOG_LEVEL"):
        try:
            data["log_level"] = int(os.environ["HOOTCAM_LOG_LEVEL"])
        except ValueError:
            pass

    # Merge with defaults (only set keys that we have)
    for k, v in data.items():
        if hasattr(defaults, k):
            setattr(defaults, k, v)

    if defaults.target_dir is None or defaults.target_dir == "":
        defaults.target_dir = str(_default_target_dir())

    return defaults


def load_camera_config(
    camera_index: int,
    config_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
    persist_config: bool = True,
) -> CameraConfig:
    """Load config for one camera. camera_index 0 or 1."""
    defaults = CameraConfig(
        camera_id=camera_index + 1,
        camera_name=f"camera{camera_index}",
    )
    data: dict[str, Any] = {}

    if db_path is None:
        db_path = get_db_path(config_dir)
    if persist_config and db_path.exists():
        raw = config_get_json(db_path, f"camera_config_{camera_index}")
        if isinstance(raw, dict):
            data = raw

    for k, v in data.items():
        if hasattr(defaults, k):
            setattr(defaults, k, v)

    return defaults


def save_global_config(
    config: GlobalConfig,
    db_path: Optional[Path] = None,
    config_dir: Optional[Path] = None,
) -> None:
    """Persist global config to DB."""
    if db_path is None:
        db_path = get_db_path(config_dir)
    init_db(db_path)
    config_set_json(db_path, "global_config", config.model_dump(exclude_none=True))


def save_camera_config(
    camera_index: int,
    config: CameraConfig,
    db_path: Optional[Path] = None,
    config_dir: Optional[Path] = None,
) -> None:
    """Persist camera config to DB."""
    if db_path is None:
        db_path = get_db_path(config_dir)
    init_db(db_path)
    config_set_json(
        db_path,
        f"camera_config_{camera_index}",
        config.model_dump(exclude_none=True),
    )


def ensure_config_dir_and_db(config_dir: Optional[Path], target_dir: Optional[str]) -> Path:
    """Ensure config/data directory and DB exist. Return db_path."""
    if config_dir is not None:
        path = Path(config_dir)
    else:
        path = Path(target_dir or _default_target_dir()) / ".hootcam_server"
    path.mkdir(parents=True, exist_ok=True)
    db_path = path / "hootcam_server.sqlite"
    init_db(db_path)
    return db_path
