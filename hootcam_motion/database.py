"""
SQLite database for events, file records, and optional config persistence.

Media files (pictures, movies) are stored on the SSD; only metadata and paths
are stored here.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

# Default DB path (can be overridden by config)
DEFAULT_DB_PATH = Path("/var/lib/hootcam-server/hootcam_server.sqlite")


def get_db_path(data_dir: Optional[Path] = None) -> Path:
    """Resolve database path. Prefer data_dir/hootcam_server.sqlite or env/config."""
    if data_dir is not None:
        return Path(data_dir) / "hootcam_server.sqlite"
    return DEFAULT_DB_PATH


def init_db(db_path: Path) -> None:
    """Create schema if not exists. Safe to call on every startup."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                camera_index INTEGER NOT NULL,
                camera_id INTEGER,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_events_camera_started ON events(camera_index, started_at);

            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER,
                camera_index INTEGER NOT NULL,
                file_type TEXT NOT NULL,
                file_path TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                frame_number INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (event_id) REFERENCES events(id)
            );
            CREATE INDEX IF NOT EXISTS idx_files_event ON files(event_id);
            CREATE INDEX IF NOT EXISTS idx_files_camera_ts ON files(camera_index, timestamp);

            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        conn.commit()
    finally:
        conn.close()


def log_event_start(
    db_path: Path,
    camera_index: int,
    camera_id: Optional[int],
    started_at: str,
) -> int:
    """Insert event and return new event id."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "INSERT INTO events (camera_index, camera_id, started_at) VALUES (?, ?, ?)",
            (camera_index, camera_id, started_at),
        )
        conn.commit()
        return cur.lastrowid or 0
    finally:
        conn.close()


def log_event_end(db_path: Path, event_id: int, ended_at: str) -> None:
    """Set ended_at for an event."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE events SET ended_at = ? WHERE id = ?", (ended_at, event_id))
        conn.commit()
    finally:
        conn.close()


def log_file(
    db_path: Path,
    event_id: Optional[int],
    camera_index: int,
    file_type: str,
    file_path: str,
    timestamp: str,
    frame_number: Optional[int] = None,
) -> int:
    """Insert file record. file_type: picture, movie, snapshot, timelapse."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            """INSERT INTO files (event_id, camera_index, file_type, file_path, timestamp, frame_number)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (event_id, camera_index, file_type, file_path, timestamp, frame_number),
        )
        conn.commit()
        return cur.lastrowid or 0
    finally:
        conn.close()


def get_events(
    db_path: Path,
    camera_index: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List events with optional camera filter."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if camera_index is not None:
            cur = conn.execute(
                """SELECT e.*, (SELECT COUNT(*) FROM files f WHERE f.event_id = e.id) AS file_count
                   FROM events e WHERE e.camera_index = ? ORDER BY e.started_at DESC LIMIT ? OFFSET ?""",
                (camera_index, limit, offset),
            )
        else:
            cur = conn.execute(
                """SELECT e.*, (SELECT COUNT(*) FROM files f WHERE f.event_id = e.id) AS file_count
                   FROM events e ORDER BY e.started_at DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_event(db_path: Path, event_id: int) -> Optional[dict[str, Any]]:
    """Get one event by id."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT * FROM events WHERE id = ?",
            (event_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_file(db_path: Path, file_id: int) -> Optional[dict[str, Any]]:
    """Get one file record by id."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_files(
    db_path: Path,
    event_id: Optional[int] = None,
    camera_index: Optional[int] = None,
    file_type: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List file records with optional filters."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conditions = []
        params: list[Any] = []
        if event_id is not None:
            conditions.append("event_id = ?")
            params.append(event_id)
        if camera_index is not None:
            conditions.append("camera_index = ?")
            params.append(camera_index)
        if file_type is not None:
            conditions.append("file_type = ?")
            params.append(file_type)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.extend([limit, offset])
        cur = conn.execute(
            f"SELECT * FROM files {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            params,
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def delete_file(db_path: Path, file_id: int) -> bool:
    """Delete a file record by id. Returns True if a row was deleted."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def config_get(db_path: Path, key: str) -> Optional[str]:
    """Get config value (JSON string)."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def config_set(db_path: Path, key: str, value: str) -> None:
    """Set config value (store as JSON string)."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def config_get_json(db_path: Path, key: str) -> Any:
    """Get config value parsed as JSON."""
    raw = config_get(db_path, key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def config_set_json(db_path: Path, key: str, value: Any) -> None:
    """Set config value (serialized as JSON)."""
    config_set(db_path, key, json.dumps(value))
