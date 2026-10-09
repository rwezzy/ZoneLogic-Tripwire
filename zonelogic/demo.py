"""Synthetic geometry fixtures, explicitly not camera footage or AI inference."""
from __future__ import annotations


def _box(x, y, w, h):
    return [round(x - w / 2, 5), round(y - h / 2, 5), round(x + w / 2, 5), round(y + h / 2, 5)]


def sources():
    access, waste = [], []
    for i in range(101):
        t = i / 5
        person_x = .1 + .8 * min(t / 18, 1)
        suitcase_x = .13 + .37 * min(max((t - 2) / 4, 0), 1) - .37 * min(max((t - 14) / 4, 0), 1)
        access.append({"timestamp": t, "detections": [
            {"class_name": "person", "confidence": .94, "bbox": _box(person_x, .32, .065, .25), "track_id": "person-1"},
            {"class_name": "suitcase", "confidence": .92, "bbox": _box(suitcase_x, .68, .09, .18), "track_id": "suitcase-1"},
        ]})
        bottle_x = .23 + .48 * min(max((t - 2) / 6, 0), 1) - .48 * min(max((t - 13) / 5, 0), 1)
        bottle_y = .34 + .32 * min(max((t - 2) / 6, 0), 1) - .32 * min(max((t - 13) / 5, 0), 1)
        waste.append({"timestamp": t, "detections": [
            {"class_name": "bottle", "confidence": .96, "bbox": _box(bottle_x, bottle_y, .045, .17), "track_id": "bottle-1"},
        ]})
    common = {"kind": "simulation", "width": 1280, "height": 720, "duration": 20,
              "video_url": None, "provenance": "Synthetic detections and schematic animation for testing. No camera footage or model inference."}
    return [
        {**common, "id": "demo-access", "name": "Protected passage · simulation", "description": "A suitcase enters a marked passage and remains there. Tests sustained occupancy.", "frames": access},
        {**common, "id": "demo-waste", "name": "Waste sorting · simulation", "description": "A bottle moves into a general-waste region. Tests an outside-to-inside transition.", "frames": waste},
    ]


def configuration(source_id):
    access = source_id == "demo-access"
    return {"source_id": source_id, "zones": [{
        "id": "passage" if access else "general-waste",
        "name": "Protected passage" if access else "General-waste bin",
        "points": [[.35, .45], [.7, .45], [.88, .96], [.12, .96]] if access else [[.55, .47], [.88, .47], [.88, .94], [.55, .94]],
    }], "rules": [{
        "id": "clear-passage" if access else "bottle-review",
        "name": "Passage obstruction candidate" if access else "Bottle in general waste",
        "zone_id": "passage" if access else "general-waste",
        "classes": ["suitcase", "backpack", "chair"] if access else ["bottle"],
        "condition": "dwell" if access else "enter", "dwell_seconds": 2,
        "cooldown_seconds": 5, "min_confidence": .4, "anchor": "bottom_center" if access else "center",
        "min_overlap": .2, "enabled": True, "actions": ["log", "sound"], "severity": "warning",
    }]}
