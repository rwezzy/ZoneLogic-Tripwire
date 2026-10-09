"""Validated public data contracts. All spatial coordinates are normalized."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .engine import validate_polygon


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @field_validator("*", mode="after")
    @classmethod
    def reject_blank_strings(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("Text fields cannot be blank")
        return value


class Detection(Contract):
    class_name: str = Field(min_length=1, max_length=80)
    confidence: float = Field(ge=0, le=1)
    bbox: tuple[float, float, float, float]
    track_id: str | int | None = None

    @field_validator("bbox")
    @classmethod
    def valid_box(cls, box):
        x1, y1, x2, y2 = box
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in box):
            raise ValueError("bbox must contain finite normalized coordinates from 0 to 1")
        if x2 <= x1 or y2 <= y1:
            raise ValueError("bbox must have positive width and height in xyxy order")
        return box

    @field_validator("class_name")
    @classmethod
    def label(cls, value):
        if not value.strip():
            raise ValueError("class_name cannot be blank")
        return value.strip().lower()


class Frame(Contract):
    timestamp: float = Field(ge=0)
    detections: list[Detection] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def track_ids(self):
        ids = [str(d.track_id) for d in self.detections if d.track_id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("Track IDs must be unique within a frame")
        return self


class DetectionDocument(Contract):
    width: int = Field(gt=0, le=16384)
    height: int = Field(gt=0, le=16384)
    duration: float = Field(gt=0, le=86400)
    frames: list[Frame] = Field(min_length=1, max_length=200000)
    provenance: str = Field(default="User-supplied detections; model provenance not verified.", max_length=2000)

    @model_validator(mode="after")
    def ordered(self):
        times = [frame.timestamp for frame in self.frames]
        if any(a >= b for a, b in zip(times, times[1:])):
            raise ValueError("Frame timestamps must be strictly increasing (seconds from clip start)")
        if times[-1] > self.duration:
            raise ValueError("A frame timestamp exceeds the clip duration")
        return self


class Zone(Contract):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    points: list[tuple[float, float]] = Field(min_length=3, max_length=64)
    source_id: str | None = None

    @field_validator("points")
    @classmethod
    def polygon(cls, points):
        validate_polygon(points)
        return points


class Rule(Contract):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=120)
    zone_id: str
    classes: list[str] = Field(min_length=1, max_length=80)
    condition: Literal["enter", "dwell", "absent"] = "dwell"
    dwell_seconds: float = Field(default=2, ge=0, le=3600)
    cooldown_seconds: float = Field(default=5, ge=0, le=3600)
    min_confidence: float = Field(default=0.4, ge=0, le=1)
    anchor: Literal["center", "bottom_center", "overlap"] = "bottom_center"
    min_overlap: float = Field(default=0.2, gt=0, le=1)
    enabled: bool = True
    actions: list[Literal["log", "sound"]] = Field(default_factory=lambda: ["log"])
    severity: Literal["info", "warning", "critical"] = "warning"

    @field_validator("classes")
    @classmethod
    def labels(cls, values):
        values = list(dict.fromkeys(v.strip().lower() for v in values))
        if any(not v or len(v) > 80 for v in values):
            raise ValueError("Provide at least one nonempty object class")
        return values


class Configuration(Contract):
    source_id: str
    zones: list[Zone] = Field(default_factory=list, max_length=30)
    rules: list[Rule] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def references(self):
        zone_ids = {z.id for z in self.zones}
        if len(zone_ids) != len(self.zones) or len({r.id for r in self.rules}) != len(self.rules):
            raise ValueError("Zone IDs and rule IDs must each be unique")
        if any(r.zone_id not in zone_ids for r in self.rules):
            raise ValueError("Every rule must reference a zone in this source")
        if any(z.source_id not in (None, self.source_id) for z in self.zones):
            raise ValueError("A zone belongs to another source")
        return self


class SessionRequest(Contract):
    source_id: str


class EvaluateRequest(Contract):
    timestamp: float = Field(ge=0)
    seek: bool = False


class IncidentUpdate(Contract):
    status: Literal["open", "resolved"]


class EvidenceRequest(Contract):
    data_url: str = Field(max_length=3_000_000)
    timestamp: float = Field(ge=0)


class VastImportRequest(Contract):
    source: str = Field(min_length=1, max_length=4000)
