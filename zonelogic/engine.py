"""Deterministic, dependency-free spatial rules for normalized detections.

All timestamps are media/capture seconds, never wall-clock receipt times. A seek or
a gap greater than ``max_frame_gap`` starts a fresh observation session. Missing
detections break a dwell interval; missing objects do not count as observed exits.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

EPSILON = 1e-9


def _number(value: Any, label: str, low: float | None = None,
            high: float | None = None) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    if low is not None and result < low:
        raise ValueError(f"{label} must be at least {low}")
    if high is not None and result > high:
        raise ValueError(f"{label} must be at most {high}")
    return result


def _sequence(value: Any, label: str) -> Sequence:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{label} must be an array")
    return value


def _name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value.strip()


def _cross(a: Sequence, b: Sequence, c: Sequence) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(point: Sequence, a: Sequence, b: Sequence) -> bool:
    return (abs(_cross(a, b, point)) <= EPSILON
            and min(a[0], b[0]) - EPSILON <= point[0] <= max(a[0], b[0]) + EPSILON
            and min(a[1], b[1]) - EPSILON <= point[1] <= max(a[1], b[1]) + EPSILON)


def _segments_intersect(a: Sequence, b: Sequence, c: Sequence, d: Sequence) -> bool:
    ab_c, ab_d = _cross(a, b, c), _cross(a, b, d)
    cd_a, cd_b = _cross(c, d, a), _cross(c, d, b)
    if ((ab_c > EPSILON and ab_d < -EPSILON or ab_c < -EPSILON and ab_d > EPSILON)
            and (cd_a > EPSILON and cd_b < -EPSILON or cd_a < -EPSILON and cd_b > EPSILON)):
        return True
    return (_on_segment(c, a, b) or _on_segment(d, a, b)
            or _on_segment(a, c, d) or _on_segment(b, c, d))


def _area(polygon: Sequence) -> float:
    return abs(sum(polygon[i][0] * polygon[(i + 1) % len(polygon)][1]
                   - polygon[(i + 1) % len(polygon)][0] * polygon[i][1]
                   for i in range(len(polygon)))) / 2 if len(polygon) >= 3 else 0.0


def validate_polygon(points: Any) -> list[list[float]]:
    """Return a normalized simple polygon; reject malformed or crossing edges.

    An optional repeated closing vertex is accepted. Concave polygons and either
    winding order are supported. Coordinates must be in the closed unit square.
    """
    result = []
    for index, raw in enumerate(_sequence(points, "polygon points")):
        pair = _sequence(raw, f"point {index}")
        if len(pair) != 2:
            raise ValueError("Each polygon point must contain exactly x and y")
        result.append([_number(pair[0], "point x", 0, 1),
                       _number(pair[1], "point y", 0, 1)])
    if len(result) > 1 and result[0] == result[-1]:
        result.pop()
    if len(result) < 3:
        raise ValueError("A polygon requires at least three distinct points")
    if len(result) > 200:
        raise ValueError("A polygon may have at most 200 points")
    if len({tuple(point) for point in result}) != len(result):
        raise ValueError("Polygon vertices must be distinct")
    n = len(result)
    for i in range(n):
        previous, current, following = result[i - 1], result[i], result[(i + 1) % n]
        # Adjacent collinear edges may continue, but may not double back.
        if abs(_cross(previous, current, following)) <= EPSILON:
            if ((previous[0] - current[0]) * (following[0] - current[0])
                    + (previous[1] - current[1]) * (following[1] - current[1])) > EPSILON:
                raise ValueError("Polygon edges may not overlap")
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue
            if _segments_intersect(result[i], result[(i + 1) % n],
                                   result[j], result[(j + 1) % n]):
                raise ValueError("Polygon edges may not intersect")
    if _area(result) <= EPSILON:
        raise ValueError("A polygon must have nonzero area")
    return result


def point_in_polygon(point: Sequence, polygon: Sequence) -> bool:
    """Even/odd ray test, with boundary points treated as inside.

    ``polygon`` should have already passed ``validate_polygon``.
    """
    x, y = point
    inside = False
    for i, a in enumerate(polygon):
        b = polygon[(i + 1) % len(polygon)]
        if _on_segment(point, a, b):
            return True
        if (a[1] > y) != (b[1] > y):
            crossing = a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if x < crossing:
                inside = not inside
    return inside


def _box(value: Any) -> list[float]:
    values = _sequence(value, "bbox")
    if len(values) != 4:
        raise ValueError("bbox must contain [x1, y1, x2, y2]")
    result = [_number(v, "bbox coordinate", 0, 1) for v in values]
    if result[2] <= result[0] or result[3] <= result[1]:
        raise ValueError("bbox must have positive width and height")
    return result


def box_overlap_ratio(box: Sequence, polygon: Sequence) -> float:
    """Fraction of bounding-box area covered by the polygon, including concavity.

    Clips the polygon against all four rectangle half-planes. The resulting path
    can include joined disjoint components; boundary joins cancel in signed area.
    """
    x1, y1, x2, y2 = _box(box)
    clipped = [list(point) for point in polygon]
    for axis, edge, direction in ((0, x1, 1), (0, x2, -1), (1, y1, 1), (1, y2, -1)):
        if not clipped:
            return 0.0
        output = []
        previous = clipped[-1]
        previous_inside = direction * (previous[axis] - edge) >= 0
        for current in clipped:
            current_inside = direction * (current[axis] - edge) >= 0
            if current_inside != previous_inside:
                fraction = (edge - previous[axis]) / (current[axis] - previous[axis])
                output.append([previous[k] + fraction * (current[k] - previous[k]) for k in (0, 1)])
            if current_inside:
                output.append(current)
            previous, previous_inside = current, current_inside
        clipped = output
    return min(1.0, max(0.0, _area(clipped) / ((x2 - x1) * (y2 - y1))))


def _iou(a: Sequence, b: Sequence) -> float:
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union else 0.0


class RuleEngine:
    """Evaluate enter, continuous dwell, and per-class absence rules.

    Cooldown is per rule and tracked object (per class for absence). An initial
    observation inside a zone is not an enter event. Empty classes or ``['*']``
    match any class; for absence they mean no matching object is present at all.
    """

    def __init__(self, zones: Sequence[Mapping], rules: Sequence[Mapping], *,
                 max_frame_gap: float = 2.0, track_ttl: float = 2.0,
                 association_iou: float = 0.1, association_distance: float = 0.15):
        self.max_frame_gap = _number(max_frame_gap, "max_frame_gap", EPSILON)
        self.track_ttl = _number(track_ttl, "track_ttl", EPSILON)
        self.association_iou = _number(association_iou, "association_iou", 0, 1)
        self.association_distance = _number(association_distance, "association_distance", 0, 2)
        self.zones: dict[str, dict] = {}
        for raw in _sequence(zones, "zones"):
            if not isinstance(raw, Mapping):
                raise ValueError("Each zone must be an object")
            zone_id = _name(raw.get("id"), "zone id")
            if zone_id in self.zones:
                raise ValueError(f"Duplicate zone id: {zone_id}")
            self.zones[zone_id] = {**deepcopy(dict(raw)), "id": zone_id,
                                   "name": _name(raw.get("name", zone_id), "zone name"),
                                   "points": validate_polygon(raw.get("points"))}
        self.rules = []
        rule_ids = set()
        for raw in _sequence(rules, "rules"):
            if not isinstance(raw, Mapping):
                raise ValueError("Each rule must be an object")
            rule = deepcopy(dict(raw))
            rule["id"] = _name(rule.get("id"), "rule id")
            if rule["id"] in rule_ids:
                raise ValueError(f"Duplicate rule id: {rule['id']}")
            rule_ids.add(rule["id"])
            rule["name"] = _name(rule.get("name", rule["id"]), "rule name")
            rule["zone_id"] = _name(rule.get("zone_id"), "zone_id")
            if rule["zone_id"] not in self.zones:
                raise ValueError(f"Unknown zone_id: {rule['zone_id']}")
            rule["condition"] = rule.get("condition", "enter")
            if rule["condition"] not in ("enter", "dwell", "absent"):
                raise ValueError("condition must be enter, dwell, or absent")
            rule["classes"] = list(dict.fromkeys(_name(c, "class").lower()
                                                   for c in _sequence(rule.get("classes", []), "classes")))
            for name, default, high in (("dwell_seconds", 3, None), ("cooldown_seconds", 5, None),
                                        ("min_confidence", .25, 1), ("min_overlap", .2, 1)):
                rule[name] = _number(rule.get(name, default), name, 0, high)
            rule["anchor"] = rule.get("anchor", "bottom_center")
            if rule["anchor"] not in ("center", "bottom_center", "overlap"):
                raise ValueError("anchor must be center, bottom_center, or overlap")
            rule["enabled"] = rule.get("enabled", True)
            if not isinstance(rule["enabled"], bool):
                raise ValueError("enabled must be a boolean")
            rule["actions"] = list(dict.fromkeys(_name(action, "action") for action in
                                                 _sequence(rule.get("actions", ["log"]), "actions")))
            if any(action not in ("log", "sound") for action in rule["actions"]):
                raise ValueError("actions may only contain log and sound")
            rule["severity"] = rule.get("severity", "warning")
            if rule["severity"] not in ("info", "warning", "critical"):
                raise ValueError("severity must be info, warning, or critical")
            self.rules.append(rule)
        self.reset()

    def reset(self) -> None:
        """Discard all tracking, occupancy, cooldown, and media-time history."""
        self._last_timestamp: float | None = None
        self._next_track = 1
        self._tracks: dict[str, dict] = {}
        self._states: dict[tuple[str, str], dict] = {}
        self._absence: dict[tuple[str, str], dict] = {}
        self._cooldowns: dict[tuple[str, str], float] = {}

    def _frame(self, frame: Any) -> tuple[float, list[dict]]:
        if not isinstance(frame, Mapping):
            raise ValueError("frame must be an object")
        timestamp = _number(frame.get("timestamp"), "timestamp", 0)
        detections, supplied_ids = [], set()
        for raw in _sequence(frame.get("detections", []), "detections"):
            if not isinstance(raw, Mapping):
                raise ValueError("Each detection must be an object")
            detection = {"class_name": _name(raw.get("class_name"), "class_name").lower(),
                         "confidence": _number(raw.get("confidence"), "confidence", 0, 1),
                         "bbox": _box(raw.get("bbox")), "track_id": None}
            if raw.get("track_id") is not None:
                supplied = raw["track_id"]
                if isinstance(supplied, bool) or not isinstance(supplied, (str, int)):
                    raise ValueError("track_id must be a string or integer")
                detection["track_id"] = _name(str(supplied), "track_id")
                if detection["track_id"] in supplied_ids:
                    raise ValueError("track_id must be unique within a frame")
                supplied_ids.add(detection["track_id"])
            detections.append(detection)
        return timestamp, detections

    def _drop_track(self, key: str) -> None:
        self._tracks.pop(key, None)
        for state_key in list(self._states):
            if state_key[1] == key:
                del self._states[state_key]
        for cooldown_key in list(self._cooldowns):
            if cooldown_key[1] == key:
                del self._cooldowns[cooldown_key]

    def _associate(self, detections: list[dict], timestamp: float) -> list[dict]:
        for key, track in list(self._tracks.items()):
            if timestamp - track["last_seen"] > self.track_ttl:
                self._drop_track(key)
        assigned, used = {}, set()
        for index, detection in enumerate(detections):
            if detection["track_id"] is not None:
                key = "supplied:" + detection["track_id"]
                if key in self._tracks and self._tracks[key]["class_name"] != detection["class_name"]:
                    self._drop_track(key)
                assigned[index] = key
                used.add(key)
        candidates = []
        for index, detection in enumerate(detections):
            if index in assigned:
                continue
            box = detection["bbox"]
            for key, track in self._tracks.items():
                if key in used or not key.startswith("inferred:") or track["class_name"] != detection["class_name"]:
                    continue
                old = track["bbox"]
                distance = math.hypot((box[0] + box[2] - old[0] - old[2]) / 2,
                                      (box[1] + box[3] - old[1] - old[3]) / 2)
                overlap = _iou(box, old)
                if ((overlap >= self.association_iou and overlap > 0)
                        or distance <= self.association_distance):
                    candidates.append((overlap, -distance, index, key))
        for _, _, index, key in sorted(candidates, reverse=True):
            if index not in assigned and key not in used:
                assigned[index] = key
                used.add(key)
        for index, detection in enumerate(detections):
            if index not in assigned:
                assigned[index] = f"inferred:{self._next_track}"
                detection["track_id"] = f"auto-{self._next_track}"
                self._next_track += 1
            key = assigned[index]
            if detection["track_id"] is None:
                detection["track_id"] = self._tracks[key]["track_id"]
            detection["_track_key"] = key
            self._tracks[key] = {**detection, "last_seen": timestamp}
        return detections

    def _inside(self, detection: dict, rule: dict) -> bool:
        polygon = self.zones[rule["zone_id"]]["points"]
        x1, y1, x2, y2 = detection["bbox"]
        if rule["anchor"] == "overlap":
            overlap = box_overlap_ratio(detection["bbox"], polygon)
            return overlap > 0 and overlap + EPSILON >= rule["min_overlap"]
        y = (y1 + y2) / 2 if rule["anchor"] == "center" else y2
        return point_in_polygon(((x1 + x2) / 2, y), polygon)

    def _event(self, rule: dict, timestamp: float, detection: dict | None,
               class_name: str, cooldown_key: str) -> dict | None:
        key = (rule["id"], cooldown_key)
        previous = self._cooldowns.get(key)
        if previous is not None and timestamp - previous + EPSILON < rule["cooldown_seconds"]:
            return None
        self._cooldowns[key] = timestamp
        zone_name = self.zones[rule["zone_id"]]["name"]
        verb = {"enter": "entered", "dwell": "remained in", "absent": "was absent from"}[rule["condition"]]
        subject = "Any matching object" if class_name == "*" else class_name.capitalize()
        duration = f" for {rule['dwell_seconds']:g}s" if rule["condition"] != "enter" else ""
        return {"rule_id": rule["id"], "rule_name": rule["name"], "zone_id": rule["zone_id"],
                "class_name": class_name, "track_id": detection["track_id"] if detection else None,
                "timestamp": timestamp, "bbox": list(detection["bbox"]) if detection else None,
                "condition": rule["condition"], "severity": rule["severity"],
                "message": f"{subject} {verb} {zone_name}{duration}.", "actions": list(rule["actions"])}

    def process(self, frame: Mapping) -> list[dict]:
        """Consume a frame once and return newly triggered event dictionaries."""
        timestamp, detections = self._frame(frame)
        if self._last_timestamp is not None:
            if timestamp == self._last_timestamp:
                return []
            if timestamp < self._last_timestamp or timestamp - self._last_timestamp > self.max_frame_gap:
                self.reset()
        self._last_timestamp = timestamp
        detections = self._associate(detections, timestamp)
        events = []
        for rule in self.rules:
            if not rule["enabled"]:
                continue
            classes = rule["classes"]
            matching = [d for d in detections if d["confidence"] >= rule["min_confidence"]
                        and (not classes or "*" in classes or d["class_name"] in classes)]
            if rule["condition"] == "absent":
                present = {d["class_name"] for d in matching if self._inside(d, rule)}
                for class_name in (["*"] if not classes or "*" in classes else classes):
                    key = (rule["id"], class_name)
                    class_is_present = bool(present) if class_name == "*" else class_name in present
                    if class_is_present:
                        self._absence.pop(key, None)
                        continue
                    state = self._absence.setdefault(key, {"since": timestamp, "fired": False})
                    if not state["fired"] and timestamp - state["since"] + EPSILON >= rule["dwell_seconds"]:
                        event = self._event(rule, timestamp, None, class_name, "absence:" + class_name)
                        if event:
                            state["fired"] = True
                            events.append(event)
                continue
            observed = set()
            for detection in matching:
                key = (rule["id"], detection["_track_key"])
                observed.add(key)
                state = self._states.setdefault(key, {"last_inside": None, "since": None, "fired": False})
                inside = self._inside(detection, rule)
                if not inside:
                    state.update(last_inside=False, since=None, fired=False)
                    continue
                crossed = state["last_inside"] is False
                if state["since"] is None:
                    state["since"] = timestamp
                state["last_inside"] = True
                trigger = (crossed if rule["condition"] == "enter" else
                           not state["fired"] and timestamp - state["since"] + EPSILON >= rule["dwell_seconds"])
                if trigger:
                    event = self._event(rule, timestamp, detection, detection["class_name"], detection["_track_key"])
                    if event:
                        state["fired"] = True
                        events.append(event)
            for key, state in self._states.items():
                if key[0] == rule["id"] and key not in observed:
                    state.update(since=None, fired=False)
        return events
