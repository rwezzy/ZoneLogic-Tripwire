"""Optional, deterministic scenes built on the unchanged spatial-rule contracts.

These are generated drawings and detections, not recorded camera footage or
material, collision, clearance, or safe-to-cross classifiers. Image coordinates
and timestamps are deliberately explicit so the rule examples are reproducible.
"""
from __future__ import annotations

from copy import deepcopy


SCENE_IDS = ("scene-sorting", "scene-wheelchair", "scene-crosswalk", "scene-cane")
FPS = 5
DURATION = 18


def _rectangle(x1, y1, x2, y2):
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _progress(timestamp, start, end):
    return min(1.0, max(0.0, (timestamp - start) / (end - start)))


def _detection(class_name, track_id, x, y, width, height):
    return {
        "class_name": class_name, "track_id": track_id, "confidence": .94,
        "bbox": [round(x - width / 2, 6), round(y - height / 2, 6),
                 round(x + width / 2, 6), round(y + height / 2, 6)],
    }


def _sorting_frame(timestamp):
    # Labels and destination assignments below are chosen by the demo author.
    # A bottle class alone cannot determine material or local recycling policy.
    detections = []
    for label, track, x, start in (
        ("bottle", "bottle-1", .265, 1),
        ("bottle", "bottle-2", .735, 7),
        ("banana", "banana-1", .815, 13),
    ):
        if timestamp >= start - 1:
            y = .23 + .34 * _progress(timestamp, start, start + 4)
            width, height = (.06, .14) if label == "bottle" else (.105, .07)
            detections.append(_detection(label, track, x, y, width, height))
    return detections


def _wheelchair_frame(timestamp):
    # The wheelchair is background context, never a generated detector class.
    x = .9 - .4 * _progress(timestamp, 2, 4)
    x += .4 * _progress(timestamp, 12, 14)
    return [_detection("suitcase", "path-bag-1", x, .58, .10, .18)]


def _crosswalk_frame(timestamp):
    x = min(.90, .12 + .08 * timestamp)
    return [_detection("car", "crosswalk-car-1", x, .54, .18, .14)]


def _cane_frame(timestamp):
    x = .9 - .4 * _progress(timestamp, 3, 6)
    x += .4 * _progress(timestamp, 12, 15)
    return [_detection("car", "cane-car-1", x, .55, .16, .14)]


def _rule(rule_id, name, zone, classes, *, condition="enter", dwell=0,
          severity="warning", sound=False):
    return {
        "id": rule_id, "name": name, "zone_id": zone, "classes": classes,
        "condition": condition, "dwell_seconds": dwell, "cooldown_seconds": 5,
        "min_confidence": .4, "anchor": "center", "enabled": True,
        "severity": severity, "actions": ["log", "sound"] if sound else ["log"],
    }


def configuration(source_id):
    """Return a fresh core Configuration payload; unknown sources get no rules.

    Optional alert metadata deliberately lives in rule_options, not in the core
    schema. A missing scenario cannot introduce invalid rules into the app.
    """
    zones, rules = [], []
    if source_id == "scene-sorting":
        zones = [
            {"id": "sort-recycling", "name": "Recycling bin",
             "points": _rectangle(.10, .43, .43, .90)},
            {"id": "sort-trash", "name": "Trash bin",
             "points": _rectangle(.57, .43, .90, .90)},
        ]
        rules = [
            _rule("sort-recycle-log", "Recycling candidate entered recycling", "sort-recycling",
                  ["bottle"], severity="info"),
            _rule("sort-trash-review", "Recycling candidate entered trash: review", "sort-trash",
                  ["bottle"], sound=True),
            _rule("sort-trash-log", "Trash candidate entered trash", "sort-trash",
                  ["banana"], severity="info"),
            _rule("sort-recycle-review", "Trash candidate entered recycling: review", "sort-recycling",
                  ["banana"], sound=True),
        ]
    elif source_id == "scene-wheelchair":
        zones = [{"id": "wheelchair-path", "name": "Marked wheelchair route",
                  "points": [[.32, .34], [.68, .34], [.82, .84], [.18, .84]]}]
        rules = [_rule("wheelchair-obstruction", "Object occupies marked route", "wheelchair-path",
                       ["suitcase", "chair", "backpack"], condition="dwell", dwell=2, sound=True)]
    elif source_id == "scene-crosswalk":
        zones = [{"id": "crosswalk-area", "name": "Marked pedestrian crossing",
                  "points": _rectangle(.39, .23, .61, .86)}]
        rules = [_rule("crosswalk-vehicle", "Vehicle entered marked crossing", "crosswalk-area",
                       ["car", "truck", "bus", "motorcycle", "bicycle"], sound=True)]
    elif source_id == "scene-cane":
        zones = [{"id": "cane-forward", "name": "Forward image region",
                  "points": [[.39, .33], [.61, .33], [.81, .90], [.19, .90]]}]
        rules = [_rule("cane-vehicle", "Vehicle in forward image region", "cane-forward",
                       ["car", "truck", "bus", "motorcycle", "bicycle"],
                       condition="dwell", dwell=.6, sound=True)]
    return {"source_id": source_id, "zones": zones, "rules": rules}


def rule_options(source_id):
    """Optional semantics and browser-vibration preferences, off by default."""
    meanings = {
        "sort-recycle-log": "recycling", "sort-recycle-review": "recycling",
        "sort-trash-log": "trash", "sort-trash-review": "trash",
        "wheelchair-obstruction": "path", "crosswalk-vehicle": "crosswalk",
        "cane-vehicle": "cane",
    }
    return {rule["id"]: {"meaning": meanings[rule["id"]], "vibrate": False,
                         "pattern": [180, 100, 180]}
            for rule in configuration(source_id)["rules"]}


_SCENES = [
    {
        "id": "scene-sorting", "name": "Recycling and trash · simulation",
        "description": "Draw bin boundaries; log a chosen recycling candidate in recycling, flag it in trash, and log a trash candidate in trash.",
        "frame_fn": _sorting_frame,
        "extra": {
            "scenario_key": "sorting",
            "scenario_notes": [
                "Generated icons and detections. Bottle is the demo's user-defined recycling candidate; banana is its trash candidate.",
                "A bin entry is a geometric observation, not proof that an item was discarded or that its material is recyclable. Local sorting policy may differ.",
                "Expected: bottle into recycling at 3.4s, bottle into trash at 9.4s, banana into trash at 15.4s.",
            ],
            "palette": {"background": "#edf3ed", "floor": "#d7e2d7", "accent": "#236c58"},
            "signage": [
                {"label": "RECYCLING", "icon": "recycle", "bounds": [.10, .43, .43, .90], "color": "#287765"},
                {"label": "TRASH", "icon": "trash", "bounds": [.57, .43, .90, .90], "color": "#596675"},
            ],
            "context_icons": [],
        },
    },
    {
        "id": "scene-wheelchair", "name": "Wheelchair route · simulation",
        "description": "An object occupies a marked route for two seconds and triggers an obstruction-candidate ticket.",
        "frame_fn": _wheelchair_frame,
        "extra": {
            "scenario_key": "wheelchair",
            "scenario_notes": [
                "The wheelchair icon gives scene context; the generated detector watches suitcase, chair, and backpack classes.",
                "Image-region occupancy is an obstruction candidate. This prototype does not measure wheelchair clearance, route width, or traversability.",
                "Expected: one route-occupancy ticket at 4.8s. The bag leaves later; no absence-of-danger signal is produced.",
            ],
            "palette": {"background": "#edf0ed", "floor": "#d8ded9", "accent": "#2a716a"},
            "signage": [{"label": "MARKED ROUTE", "icon": "wheelchair", "bounds": [.18, .34, .82, .84], "color": "#2a716a"}],
            "context_icons": [{"icon": "wheelchair", "x": .5, "y": .92, "scale": .10}],
        },
    },
    {
        "id": "scene-crosswalk", "name": "Crosswalk vehicle · simulation",
        "description": "A car crosses a user-marked pedestrian region in a fixed-camera illustration.",
        "frame_fn": _crosswalk_frame,
        "extra": {
            "scenario_key": "crosswalk",
            "scenario_notes": [
                "A fixed-camera polygon can be drawn over a crosswalk or other public-space region, not only a warehouse.",
                "Vehicle presence in an image region is a prototype cue. It does not establish collision risk, distance, right of way, or permission to cross.",
                "Expected: one vehicle-entry ticket at 3.4s; optional browser vibration requires supported hardware and explicit enabling.",
            ],
            "palette": {"background": "#dde8e7", "floor": "#596575", "accent": "#d3aa58"},
            "signage": [{"label": "CROSSWALK", "icon": "crosswalk", "bounds": [.39, .23, .61, .86], "color": "#f4f4ec"}],
            "context_icons": [],
        },
    },
    {
        "id": "scene-cane", "name": "Cane camera cue · simulation",
        "description": "A vehicle enters a forward image region; after 0.6 seconds it creates a prototype cue, with optional browser vibration.",
        "frame_fn": _cane_frame,
        "extra": {
            "scenario_key": "cane",
            "scenario_notes": [
                "This generated first-person illustration demonstrates an event-to-alert connection; it is not a live walking-stick camera feed.",
                "Camera motion changes the meaning of a fixed image polygon. A real moving-camera system needs motion compensation, calibration, and validated sensing.",
                "No distance, collision, clear-path, or safe-to-cross inference is made. Browser vibration is optional; no walking-stick hardware is connected.",
                "Expected: one forward-region ticket at 5.2s. Unsupported or disabled vibration leaves the incident and visual cue available.",
            ],
            "palette": {"background": "#dce8ed", "floor": "#b7c6c0", "accent": "#725e9b"},
            "signage": [{"label": "FORWARD IMAGE REGION", "icon": "cane", "bounds": [.19, .33, .81, .90], "color": "#725e9b"}],
            "context_icons": [{"icon": "cane", "x": .88, "y": .87, "scale": .15}],
        },
    },
]


def sources():
    """Generate fresh 5fps core-compatible sources with opt-in drawing metadata."""
    result = []
    for scene in _SCENES:
        source = deepcopy({key: value for key, value in scene.items() if key != "frame_fn"})
        source.update(kind="simulation", width=1280, height=720, duration=DURATION,
                      video_url=None,
                      provenance="Generated icon scene and scripted detections; no camera footage or model inference.")
        source["frames"] = [
            {"timestamp": index / FPS, "detections": scene["frame_fn"](index / FPS)}
            for index in range(DURATION * FPS + 1)
        ]
        result.append(source)
    return result
