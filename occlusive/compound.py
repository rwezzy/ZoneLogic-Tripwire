"""Two-input Boolean logic evaluated on one normalized detection frame."""
from copy import deepcopy
import hashlib
import json
from typing import Literal

from pydantic import Field, StrictBool, field_validator, model_validator
from zonelogic.models import Contract, Frame, Zone
from zonelogic.engine import point_in_polygon


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Predicate(Contract):
    class_name: str = Field(min_length=1, max_length=80)
    zone_id: str = Field(min_length=1, max_length=100)
    min_confidence: float = Field(default=.4, ge=0, le=1)
    negate: StrictBool = False

    @field_validator("class_name")
    @classmethod
    def label(cls, value):
        value = value.strip().lower()
        if not value or value == "*":
            raise ValueError("Select one actual class label")
        return value


class CompoundRule(Contract):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=120)
    a: Predicate
    b: Predicate
    operator: Literal["AND", "OR", "XOR"] = "AND"
    dwell_seconds: float = Field(default=0, ge=0, le=3600)
    cooldown_seconds: float = Field(default=5, ge=0, le=3600)
    enabled: StrictBool = True
    actions: list[Literal["log", "sound"]] = Field(default_factory=lambda: ["log"])
    severity: Literal["info", "warning", "critical"] = "warning"


class CompoundConfiguration(Contract):
    source_id: str = Field(min_length=1, max_length=200)
    rules: list[CompoundRule] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique(self):
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError("Compound rule IDs must be unique")
        return self


def combine(a: bool, b: bool, operator: str) -> bool:
    if operator == "AND":
        return a and b
    if operator == "OR":
        return a or b
    if operator == "XOR":
        return (a and not b) or (b and not a)
    raise ValueError("Operator must be AND, OR, or XOR")


class CompoundEngine:
    def __init__(self, zones, rules, *, max_frame_gap=2.0):
        self.zones = {zone.id: zone.model_dump(mode="json") for zone in map(Zone.model_validate, zones)}
        self.rules = [CompoundRule.model_validate(rule).model_dump(mode="json") for rule in rules]
        self.max_frame_gap = max_frame_gap
        self.config_hash = digest({"zones": self.zones, "rules": self.rules})
        self.reset()

    def reset(self):
        self.last_timestamp = None
        self.temporal = {}
        self.values = []

    def predicate(self, predicate, detections):
        zone = self.zones.get(predicate["zone_id"])
        if zone is None:
            return None, []
        matched = []
        for detection in detections:
            if detection["class_name"] != predicate["class_name"] or detection["confidence"] < predicate["min_confidence"]:
                continue
            x1, y1, x2, y2 = detection["bbox"]
            if point_in_polygon(((x1 + x2) / 2, (y1 + y2) / 2), zone["points"]):
                matched.append(detection)
        present = bool(matched)
        return (not present if predicate["negate"] else present), matched

    def process(self, frame):
        frame = Frame.model_validate(frame).model_dump(mode="json")
        timestamp = frame["timestamp"]
        if self.last_timestamp is not None:
            if timestamp == self.last_timestamp:
                return []
            if timestamp < self.last_timestamp or timestamp - self.last_timestamp > self.max_frame_gap:
                self.reset()
        self.last_timestamp = timestamp
        self.values = []
        events = []
        for rule in self.rules:
            a, matches_a = self.predicate(rule["a"], frame["detections"])
            b, matches_b = self.predicate(rule["b"], frame["detections"])
            valid = rule["enabled"] and a is not None and b is not None
            result = combine(a, b, rule["operator"]) if valid else None
            self.values.append({"rule_id": rule["id"], "name": rule["name"], "a": a if valid else None,
                                "b": b if valid else None, "result": result, "timestamp": timestamp,
                                "operator": rule["operator"], "not_a": rule["a"]["negate"], "not_b": rule["b"]["negate"],
                                "raw_a": bool(matches_a) if valid else None, "raw_b": bool(matches_b) if valid else None})
            if not valid:
                self.temporal.pop(rule["id"], None)
                continue
            previous = self.temporal.get(rule["id"])
            if previous is None:
                # An initially true frame (including after a seek/gap) is a
                # baseline, not an observed false-to-true transition.
                self.temporal[rule["id"]] = {"result": result, "since": None, "last_event": None}
                continue
            if not result:
                previous["since"] = None
            elif not previous["result"]:
                previous["since"] = timestamp
            previous["result"] = result
            start = previous["since"]
            if result and start is not None and timestamp - start + 1e-9 >= rule["dwell_seconds"]:
                previous["since"] = None  # once per truth episode, even if suppressed
                if previous["last_event"] is not None and timestamp - previous["last_event"] < rule["cooldown_seconds"]:
                    continue
                previous["last_event"] = timestamp
                expression = f"{'NOT ' if rule['a']['negate'] else ''}A {rule['operator']} {'NOT ' if rule['b']['negate'] else ''}B"
                meaning = "Potential co-occupancy for human review." if rule["operator"] == "AND" and not rule["a"]["negate"] and not rule["b"]["negate"] else "Boolean detection condition for human review."
                events.append({"rule_id": rule["id"], "rule_name": rule["name"], "zone_id": rule["a"]["zone_id"],
                    "class_name": f"{rule['a']['class_name']} / {rule['b']['class_name']}", "condition": "compound",
                    "timestamp": timestamp, "track_id": None, "confidence": None,
                    "message": f"{expression} became true for {rule['dwell_seconds']:g}s. {meaning}",
                    "severity": rule["severity"], "actions": rule["actions"],
                    "compound": {"expression": expression, "a": a, "b": b, "result": True,
                        "raw_a": bool(matches_a), "raw_b": bool(matches_b), "transition_timestamp": start,
                        "frame_timestamp": timestamp, "rule": deepcopy(rule), "configuration_hash": self.config_hash,
                        "zones": [deepcopy(self.zones[rule[key]["zone_id"]]) for key in ("a", "b")],
                        "matches_a": matches_a, "matches_b": matches_b}})
        return events


class CombinedEngine:
    """Both engines receive the exact frame selected by the original playback API."""
    def __init__(self, original, compound):
        self.original, self.compound = original, compound

    def reset(self):
        self.original.reset()
        self.compound.reset()

    def process(self, frame):
        return self.original.process(frame) + self.compound.process(frame)
