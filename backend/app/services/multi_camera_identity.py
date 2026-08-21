"""Cross-camera global identity, BLE proximity hints, and automatic layout learning."""

from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment


def _cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    if left.shape != right.shape:
        return 0.0
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator <= 1e-8:
        return 0.0
    return float(np.clip(np.dot(left, right) / denominator, 0.0, 1.0))


def receiver_camera_id(receiver_id: str) -> str:
    suffix = receiver_id.rsplit("-", 1)[-1]
    return f"camera-{suffix}" if suffix.isdigit() else receiver_id


@dataclass
class GlobalIdentity:
    global_id: int
    track_id: str
    embedding_gallery: list[np.ndarray] = field(default_factory=list, repr=False)
    last_seen_at: float = 0.0
    last_camera_id: str | None = None
    last_seen_by_camera: dict[str, float] = field(default_factory=dict)
    ble_tag_key: str | None = None

    def similarity(self, embedding: np.ndarray) -> float:
        scores = sorted(
            (_cosine_similarity(saved, embedding) for saved in self.embedding_gallery),
            reverse=True,
        )
        strongest = scores[: min(3, len(scores))]
        return float(sum(strongest) / len(strongest)) if strongest else 0.0

    def update_embedding(self, embedding: np.ndarray, quality: float) -> None:
        if quality < 0.4:
            return
        self.embedding_gallery.append(embedding.copy())
        self.embedding_gallery = self.embedding_gallery[-16:]


class MultiCameraIdentityManager:
    """Fuse local camera tracks into session-scoped global person IDs."""

    APPEARANCE_GATE = 0.62
    BLE_APPEARANCE_GATE = 0.52
    MATCH_THRESHOLD = 0.68
    OVERLAP_WINDOW_SECONDS = 2.0
    HANDOFF_WINDOW_SECONDS = 30.0
    VISIBLE_SECONDS = 2.5

    def __init__(self):
        self._lock = threading.RLock()
        self._next_global_id = 1
        self._identities: dict[int, GlobalIdentity] = {}
        self._local_to_global: dict[tuple[str, int], int] = {}
        self._receiver_strengths: dict[str, dict[str, int]] = {}
        self._overlap_evidence = 0
        self._handoff_evidence = 0
        self._visual_samples = 0
        self._visual_overlap_votes = 0
        self._last_visual_check_at = 0.0

    def update_receiver_strengths(
        self,
        strengths: dict[str, dict[str, int]],
    ) -> None:
        with self._lock:
            self._receiver_strengths = {
                tag_key: dict(receivers)
                for tag_key, receivers in strengths.items()
            }

    def _strongest_camera_for_tag(self, tag_key: str | None) -> str | None:
        if not tag_key:
            return None
        receivers = self._receiver_strengths.get(tag_key, {})
        if not receivers:
            return None
        receiver_id = max(receivers, key=receivers.get)
        return receiver_camera_id(receiver_id)

    def _new_identity(self, track, camera_id: str, now: float) -> GlobalIdentity:
        global_id = self._next_global_id
        self._next_global_id += 1
        identity = GlobalIdentity(
            global_id=global_id,
            track_id=f"person-{global_id:06d}",
            embedding_gallery=[track.embedding.copy()],
            last_seen_at=now,
            last_camera_id=camera_id,
            last_seen_by_camera={camera_id: now},
        )
        self._identities[global_id] = identity
        return identity

    def _candidate_score(
        self,
        identity: GlobalIdentity,
        track,
        camera_id: str,
        now: float,
    ) -> float:
        appearance = identity.similarity(track.embedding)
        strongest_camera = self._strongest_camera_for_tag(identity.ble_tag_key)
        ble_near_camera = strongest_camera == camera_id
        appearance_gate = (
            self.BLE_APPEARANCE_GATE
            if ble_near_camera
            else self.APPEARANCE_GATE
        )
        if appearance < appearance_gate:
            return -1.0

        elapsed = max(0.0, now - identity.last_seen_at)
        if elapsed > self.HANDOFF_WINDOW_SECONDS:
            return -1.0

        if identity.last_camera_id == camera_id:
            temporal = 0.12
        elif elapsed <= self.OVERLAP_WINDOW_SECONDS:
            temporal = 0.10
        else:
            temporal = 0.08 * max(
                0.0,
                1.0 - elapsed / self.HANDOFF_WINDOW_SECONDS,
            )

        ble_bonus = 0.18 if ble_near_camera else 0.0

        return 0.78 * appearance + temporal + ble_bonus

    def resolve(
        self,
        camera_id: str,
        tracks: list,
        now: float | None = None,
    ) -> list:
        now = time.monotonic() if now is None else now
        with self._lock:
            assigned: dict[int, int] = {}
            used_global: set[int] = set()

            for index, track in enumerate(tracks):
                global_id = self._local_to_global.get((camera_id, track.local_track_id))
                if global_id in self._identities and global_id not in used_global:
                    assigned[index] = global_id
                    used_global.add(global_id)

            pending_indexes = [
                index for index in range(len(tracks))
                if index not in assigned
            ]
            candidates = [
                identity for identity in self._identities.values()
                if identity.global_id not in used_global
                and now - identity.last_seen_at <= self.HANDOFF_WINDOW_SECONDS
            ]

            if pending_indexes and candidates:
                scores = np.full(
                    (len(candidates), len(pending_indexes)),
                    -1.0,
                    dtype=np.float32,
                )
                for row, identity in enumerate(candidates):
                    for column, track_index in enumerate(pending_indexes):
                        scores[row, column] = self._candidate_score(
                            identity,
                            tracks[track_index],
                            camera_id,
                            now,
                        )
                rows, columns = linear_sum_assignment(-scores)
                for row, column in zip(rows, columns):
                    if float(scores[row, column]) < self.MATCH_THRESHOLD:
                        continue
                    track_index = pending_indexes[column]
                    identity = candidates[row]
                    assigned[track_index] = identity.global_id
                    used_global.add(identity.global_id)

            resolved = []
            for index, track in enumerate(tracks):
                global_id = assigned.get(index)
                if global_id is None:
                    identity = self._new_identity(track, camera_id, now)
                    global_id = identity.global_id
                    assigned[index] = global_id
                else:
                    identity = self._identities[global_id]

                previous_other_times = [
                    timestamp
                    for other_camera, timestamp in identity.last_seen_by_camera.items()
                    if other_camera != camera_id
                ]
                if previous_other_times:
                    gap = now - max(previous_other_times)
                    if 0.0 <= gap <= self.OVERLAP_WINDOW_SECONDS:
                        self._overlap_evidence += 1
                    elif gap <= self.HANDOFF_WINDOW_SECONDS:
                        self._handoff_evidence += 1

                self._local_to_global[(camera_id, track.local_track_id)] = global_id
                identity.last_seen_at = now
                identity.last_camera_id = camera_id
                identity.last_seen_by_camera[camera_id] = now
                identity.update_embedding(track.embedding, float(track.quality))

                view = copy.copy(track)
                view.local_track_id = identity.global_id
                view.track_id = identity.track_id
                view.ble_tag_key = identity.ble_tag_key
                resolved.append(view)

            self._purge_stale(now)
            return sorted(resolved, key=lambda track: track.local_track_id)

    def _purge_stale(self, now: float) -> None:
        stale_ids = [
            global_id
            for global_id, identity in self._identities.items()
            if (
                not identity.ble_tag_key
                and now - identity.last_seen_at > 120.0
            )
        ]
        for global_id in stale_ids:
            self._identities.pop(global_id, None)
        if stale_ids:
            stale = set(stale_ids)
            self._local_to_global = {
                key: global_id
                for key, global_id in self._local_to_global.items()
                if global_id not in stale
            }

    def merge_track_ids(
        self,
        primary_track_id: str,
        duplicate_track_ids: list[str] | tuple[str, ...] | set[str],
    ) -> list[str]:
        """Merge duplicate global identities and rewrite every local-camera mapping."""
        with self._lock:
            target = next(
                (
                    identity for identity in self._identities.values()
                    if identity.track_id == primary_track_id
                ),
                None,
            )
            if target is None:
                raise ValueError("유지할 작업자 ID가 더 이상 활성 상태가 아닙니다.")

            requested = {
                track_id
                for track_id in duplicate_track_ids
                if track_id != primary_track_id
            }
            duplicates = [
                identity
                for identity in self._identities.values()
                if identity.track_id in requested
            ]
            tag_keys = {
                identity.ble_tag_key
                for identity in [target, *duplicates]
                if identity.ble_tag_key
            }
            if len(tag_keys) > 1:
                raise ValueError("서로 다른 BLE 태그가 등록된 ID는 병합할 수 없습니다.")

            merged_track_ids = []
            for duplicate in duplicates:
                merged_track_ids.append(duplicate.track_id)
                target.embedding_gallery.extend(
                    embedding.copy()
                    for embedding in duplicate.embedding_gallery
                )
                target.embedding_gallery = target.embedding_gallery[-16:]
                if duplicate.last_seen_at > target.last_seen_at:
                    target.last_seen_at = duplicate.last_seen_at
                    target.last_camera_id = duplicate.last_camera_id
                for camera_id, seen_at in duplicate.last_seen_by_camera.items():
                    target.last_seen_by_camera[camera_id] = max(
                        seen_at,
                        target.last_seen_by_camera.get(camera_id, 0.0),
                    )
                if not target.ble_tag_key:
                    target.ble_tag_key = duplicate.ble_tag_key
                self._local_to_global = {
                    local_key: (
                        target.global_id
                        if global_id == duplicate.global_id
                        else global_id
                    )
                    for local_key, global_id in self._local_to_global.items()
                }
                self._identities.pop(duplicate.global_id, None)

            return sorted(merged_track_ids)

    def bind_tag(self, tag_key: str, track_id: str) -> bool:
        with self._lock:
            target = next(
                (
                    identity for identity in self._identities.values()
                    if identity.track_id == track_id
                ),
                None,
            )
            if target is None:
                return False
            for identity in self._identities.values():
                if identity.ble_tag_key == tag_key or identity.global_id == target.global_id:
                    identity.ble_tag_key = None
            target.ble_tag_key = tag_key
            return True

    def unbind_tag(self, tag_key: str) -> None:
        with self._lock:
            for identity in self._identities.values():
                if identity.ble_tag_key == tag_key:
                    identity.ble_tag_key = None

    def forget_camera(self, camera_id: str) -> None:
        """Forget local tracker keys while retaining identities for handoff."""
        with self._lock:
            self._local_to_global = {
                key: global_id
                for key, global_id in self._local_to_global.items()
                if key[0] != camera_id
            }

    def is_visible(self, track_id: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            return any(
                identity.track_id == track_id
                and now - identity.last_seen_at <= self.VISIBLE_SECONDS
                for identity in self._identities.values()
            )

    def observe_visual_pair(
        self,
        left_frame: np.ndarray,
        right_frame: np.ndarray,
        now: float | None = None,
    ) -> bool | None:
        """Return whether two frames share a reliable static-scene homography."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if now - self._last_visual_check_at < 1.5:
                return None
            self._last_visual_check_at = now

        def prepare(frame: np.ndarray) -> np.ndarray:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            height, width = gray.shape
            if width > 480:
                resized_height = max(1, round(height * 480 / width))
                gray = cv2.resize(gray, (480, resized_height))
            return gray

        left = prepare(left_frame)
        right = prepare(right_frame)
        orb = cv2.ORB_create(nfeatures=700)
        left_points, left_desc = orb.detectAndCompute(left, None)
        right_points, right_desc = orb.detectAndCompute(right, None)
        overlap = False
        if (
            left_desc is not None
            and right_desc is not None
            and len(left_points) >= 20
            and len(right_points) >= 20
        ):
            matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
            pairs = matcher.knnMatch(left_desc, right_desc, k=2)
            good = [
                first for first, second in pairs
                if first.distance < 0.72 * second.distance
            ]
            if len(good) >= 18:
                source = np.float32(
                    [left_points[match.queryIdx].pt for match in good]
                ).reshape(-1, 1, 2)
                target = np.float32(
                    [right_points[match.trainIdx].pt for match in good]
                ).reshape(-1, 1, 2)
                _, mask = cv2.findHomography(source, target, cv2.RANSAC, 4.0)
                inliers = int(mask.sum()) if mask is not None else 0
                overlap = inliers >= 12 and inliers / len(good) >= 0.35

        with self._lock:
            self._visual_samples += 1
            if overlap:
                self._visual_overlap_votes += 1
        return overlap

    def layout_status(self) -> dict:
        with self._lock:
            if self._visual_overlap_votes >= 2 or self._overlap_evidence >= 3:
                mode = "overlap"
                confidence = min(
                    0.99,
                    0.65
                    + 0.08 * self._visual_overlap_votes
                    + 0.02 * self._overlap_evidence,
                )
            elif (
                self._handoff_evidence >= 1
                or (
                    self._visual_samples >= 8
                    and self._visual_overlap_votes == 0
                )
            ):
                mode = "separate"
                confidence = min(
                    0.95,
                    0.62
                    + 0.08 * self._handoff_evidence
                    + 0.02 * max(0, self._visual_samples - 8),
                )
            else:
                mode = "calibrating"
                confidence = 0.0

            return {
                "mode": mode,
                "confidence": round(confidence, 2),
                "visual_samples": self._visual_samples,
                "visual_overlap_votes": self._visual_overlap_votes,
                "overlap_evidence": self._overlap_evidence,
                "handoff_evidence": self._handoff_evidence,
            }

    def reset_layout(self) -> None:
        with self._lock:
            self._overlap_evidence = 0
            self._handoff_evidence = 0
            self._visual_samples = 0
            self._visual_overlap_votes = 0
            self._last_visual_check_at = 0.0

    def flush(self) -> None:
        with self._lock:
            self._next_global_id = 1
            self._identities.clear()
            self._local_to_global.clear()
            self._receiver_strengths.clear()
            self.reset_layout()
