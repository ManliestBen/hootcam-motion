"""
Motion detection: frame-difference with threshold and noise filtering.

Options: threshold, noise_level, minimum_motion_frames, despeckle (simplified),
and event_gap for grouping detections into events.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


def _to_grayscale(arr: np.ndarray) -> np.ndarray:
    """Convert RGB to grayscale (0-255)."""
    if arr.ndim == 3:
        return np.dot(arr[..., :3], [0.299, 0.587, 0.114]).astype(np.uint8)
    return arr.astype(np.uint8)


def _noise_filter(
    diff: np.ndarray,
    noise_level: int,
) -> np.ndarray:
    """Zero out changes smaller than noise_level (pixel intensity delta)."""
    return np.where(diff >= noise_level, diff, 0).astype(np.uint8)


def _despeckle_simple(diff: np.ndarray, method: Optional[str]) -> np.ndarray:
    """
    Simple despeckle: remove isolated pixels by requiring a neighbor.
    method: e.g. 'EedDl' - we do a minimal cleanup when set; full E/e/D/d/l not implemented.
    """
    if not method or method == "off":
        return diff
    binary = (diff > 0).astype(np.uint8)
    pad = np.pad(binary, 1, mode="constant", constant_values=0)
    neighbor_sum = (
        pad[0:-2, 0:-2] + pad[0:-2, 1:-1] + pad[0:-2, 2:]
        + pad[1:-1, 0:-2] + pad[1:-1, 2:]
        + pad[2:, 0:-2] + pad[2:, 1:-1] + pad[2:, 2:]
    )
    keep = (binary > 0) & (neighbor_sum > 0)
    return np.where(keep, diff, 0).astype(np.uint8)


def count_changed_pixels(
    current: np.ndarray,
    reference: np.ndarray,
    noise_level: int = 32,
    despeckle_filter: Optional[str] = None,
) -> int:
    """
    Compare current and reference frames; return number of changed pixels
    after noise filtering and optional despeckle.
    """
    cur_g = _to_grayscale(current)
    ref_g = _to_grayscale(reference)
    diff = np.abs(np.int16(cur_g) - np.int16(ref_g))
    diff = _noise_filter(diff, noise_level)
    diff = _despeckle_simple(diff, despeckle_filter)
    return int(np.count_nonzero(diff > 0))


class MotionDetector:
    """
    Stateful motion detector: maintains reference frame, counts consecutive
    motion frames, and reports when threshold is exceeded.
    """

    def __init__(
        self,
        threshold: int = 1500,
        threshold_maximum: int = 0,
        noise_level: int = 32,
        despeckle_filter: Optional[str] = None,
        minimum_motion_frames: int = 1,
    ) -> None:
        self.threshold = threshold
        self.threshold_maximum = threshold_maximum
        self.noise_level = noise_level
        self.despeckle_filter = despeckle_filter
        self.minimum_motion_frames = minimum_motion_frames
        self._reference: Optional[np.ndarray] = None
        self._motion_frame_count = 0
        self._changed_pixels = 0

    def update(
        self,
        frame: np.ndarray,
    ) -> tuple[bool, int]:
        """
        Update with new frame. Returns (motion_detected, changed_pixel_count).
        motion_detected is True when changed pixels >= threshold (and
        <= threshold_maximum if set) for minimum_motion_frames in a row.
        """
        if self._reference is None:
            self._reference = frame.copy()
            return False, 0

        changed = count_changed_pixels(
            frame,
            self._reference,
            noise_level=self.noise_level,
            despeckle_filter=self.despeckle_filter,
        )
        self._changed_pixels = changed

        above_min = changed >= self.threshold
        below_max = self.threshold_maximum <= 0 or changed <= self.threshold_maximum
        triggered = above_min and below_max

        if triggered:
            self._motion_frame_count += 1
            # Update reference slowly so we don't lose tracking
            self._reference = (
                0.95 * self._reference.astype(np.float32)
                + 0.05 * frame.astype(np.float32)
            ).astype(np.uint8)
        else:
            self._motion_frame_count = 0
            self._reference = frame.copy()

        motion_detected = (
            self._motion_frame_count >= self.minimum_motion_frames and triggered
        )
        return motion_detected, changed

    def reset_reference(self, frame: Optional[np.ndarray] = None) -> None:
        """Reset reference frame (e.g. at event end)."""
        if frame is not None:
            self._reference = frame.copy()
        else:
            self._reference = None
        self._motion_frame_count = 0

    @property
    def changed_pixels(self) -> int:
        return self._changed_pixels
