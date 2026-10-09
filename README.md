# ZoneLogic

Turn video detections into spatial rules and reviewable incidents. Draw a polygon, choose object classes, and flag an entry, sustained occupancy, or prolonged absence. The same engine supports a protected passage and a user-defined waste-sorting check.

This is a hackathon prototype for human review. It does not measure wheelchair clearance, certify accessibility, identify recycling materials, or operate as a safety system.

## Run locally

Python 3.11 or newer:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python run.py
```

On macOS/Linux, use `.venv/bin/python` instead. Open **http://127.0.0.1:8000**. This repository also runs with `python run.py` if the requirements are already installed. If the port is occupied, use `python run.py --port 8090` and open **http://127.0.0.1:8090**.

The two built-in sources are **explicitly labeled simulations**. They use generated detection coordinates and schematic drawings, not actual camera footage or YOLO inference. They make the full zone → rule → incident → evidence → resolution workflow testable without credentials. A passage incident appears after a suitcase stays inside the zone for two seconds; the bottle scenario flags a true outside-to-inside transition.

## Use real VAST video

Run this project **inside your assigned workshop VM**, where the VSS service and team configuration are available. Do not paste credentials into the frontend or commit them.

The server reads these environment variables:

| Variable | Purpose |
| --- | --- |
| `VSS_URL` or `INGRESS_URL` | VSS service base URL, without `/api/v1` |
| `VSS_USERNAME`, `VSS_PASSWORD` | Team VSS login |
| `USERNAME`, `PASSWORD` | Existing workshop aliases, used when `PASSWORD` is present |
| `YOLO_URL`, `GPU_BEARER_TOKEN` | Optional direct video inference adapter |
| `HOST`, `PORT` | Defaults `127.0.0.1`, `8000`; container uses `0.0.0.0`, `8080` |
| `ZONELOGIC_DATA_DIR` | Persistent sources, screenshots, incidents; defaults `data` |

`.env.example` lists variable names only; the server reads the process environment, **not `.env` automatically**. On the workshop VM, use the organizer's existing configuration-loading instructions. Never print configuration files containing secrets.

First verify the real retrieval path:

```bash
python -m scripts.probe_workshop --location warehouse3
```

This authenticates, lists actual archive segments, retrieves metadata and a stored detection sidecar, downloads one segment, and writes its video and normalized detections under `data/probe/`. It reports real frame counts, classes, dimensions, and timing. If that location has no suitable clips, try `--location indoor` or omit the location. It fails clearly when configuration, sidecars, dimensions, or timing are unavailable.

Then run the app and use **Browse VAST**. Select and import a real segment. Draw a zone for that particular camera view and create a rule using classes that actually appear in the detections. The app downloads a bounded segment to its local cache, keeping service tokens out of browser URLs. Source metadata retains the Cosmos description when present.

The adapter was implemented against the official public source and tested with contract fixtures. A live workshop probe must pass before claiming an actual VAST demonstration. It does not modify ingestion or train a model.

## Upload your own video and detections

Use the upload form with an MP4/WebM and matching JSON. Uploading a video alone does not run a detector. The normalized JSON schema is:

```json
{
  "width": 1280,
  "height": 720,
  "duration": 2.0,
  "provenance": "Describe how these detections were obtained",
  "frames": [
    {
      "timestamp": 0.0,
      "detections": [
        {
          "class_name": "bottle",
          "confidence": 0.92,
          "bbox": [0.1, 0.2, 0.2, 0.6],
          "track_id": "bottle-1"
        }
      ]
    },
    {"timestamp": 1.0, "detections": []}
  ]
}
```

Boxes use normalized `[left, top, right, bottom]`, not pixel `xywh`. Timestamps are strictly increasing **seconds from this clip's beginning**, not parent-video or wall-clock time. Include empty detection frames: missing frames are not evidence of an empty scene. Tracking IDs are optional; the engine uses approximate class-aware association when absent. This can fail at occlusions or crowded crossings.

Official VSS sidecars are also accepted when they supply dimensions and timing. Their pixel `bbox` and `time_sec` fields are converted explicitly. The adapter rejects unknown formats rather than guessing.

An optional documented whole-video YOLO client exists as `VastClient.infer_clip`. It is not exposed as live webcam support. The official service accepts `video_base64` at `/v1/infer`; a single-image endpoint has not been established. Browser webcam processing is intentionally outside this version.

## Rule behavior

- **Entry:** requires the same object to be observed outside and then inside. An object already inside on the first frame is not a crossing.
- **Dwell:** requires continuous qualifying detections for the configured time. Emits once per occupancy episode; cooldown prevents a rapid re-entry from spamming tickets.
- **Absence:** alerts when a requested class is absent for the configured duration. Multiple requested classes are evaluated separately.
- **Geometry:** choose box center, bottom center, or polygon intersection as a fraction of the box area. Simple concave polygons are supported; self-crossing and degenerate polygons are rejected.
- **Gaps:** seeks and gaps longer than two seconds reset temporal state. Missing/low-confidence detections break continuous dwell. Stable inference identifiers are preferred over approximate association.
- **Replay:** incidents are deduplicated by source, saved configuration, and event evidence. Rewatching a clip retains the same ticket and resolution state. Stored tickets remain visible without sounding again.

Sound requires a user interaction and browser audio permission. The inbox always remains the durable action. Screenshot evidence records its own capture timestamp and is accepted only within 0.75 seconds of the event; geometric evidence is retained even without an image. Simulation screenshots are marked as simulations.

## Architecture

```text
VSS segment + timed detection sidecar     Upload + detection JSON     Simulation
                     \                       |                       /
                      normalized frames and source-local timestamps
                                         |
                          polygon geometry + temporal tracker
                                         |
                          class, entry, dwell, absence rules
                                         |
                        SQLite tickets + evidence + dashboard
```

`zonelogic/engine.py` has no server or video dependency. `vast.py` handles VSS auth, retrieval, strict sidecar normalization, and optional video inference. `main.py` exposes the API and processes recorded frames as playback advances. `static/` contains buildless HTML/CSS/JavaScript. SQLite persists source caches, configuration, incidents, and evidence.

This version monitors while playback is running in the browser. It is not a background camera service. Run **one server worker** because temporal playback sessions live in memory; restart resets sessions while retaining stored configuration and incidents. Pause before editing; saving rules starts a new temporal evaluation context.

## Verify

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
node --check zonelogic/static/app.js
```

Tests cover geometry, temporal transitions, identity association, malformed input, API persistence, replay, seeking, evidence, resolution, VSS authentication refresh, and exact sidecar contracts. Fixture-based VSS tests do not prove access to the team's live services.

`node scripts/browser_smoke.cjs` is an optional real-browser integration test when Playwright and Edge are installed. Set `PLAYWRIGHT_MODULE` to an existing Playwright package directory if it is not installed locally. It starts its own isolated localhost server/database, tests desktop/mobile layouts, polygon persistence, both event types, screenshot viewing, incident resolution, video upload/decoding and HTTP range requests, then stops its server. Generated test data and screenshots stay under ignored `artifacts/`.

## Workshop deployment

Use the organizer's **deployment/deploy-app-no-registry** skill from `~/vast-builders-challenge/.cursor/skills/`, with this directory as the application source. A deployment preparer packages the source into a size-checked ConfigMap and uses the public Python runtime image, so no image build or registry is needed:

```bash
python -m scripts.prepare_deployment --namespace team-20 --secret zonelogic-vss-creds
```

Use your assigned team namespace (the supplied brief says Team 20). Review `artifacts/deployment/manifest.json` and `DEPLOY.txt` on the VM. They contain the Secret setup, apply and verification commands. The script **only prepares files**, never calls Kubernetes or embeds credentials. Add `--data-pvc EXISTING_CLAIM` if the team has persistent storage; default `emptyDir` loses local state when a pod is replaced.

The app supports `/health`, `/`, and `/app/`. Relative asset/API URLs work behind the workshop prefix. Preserve the workshop's configured audience and ingress controls. The separate Dockerfile is available for ordinary container builds, but is not required by the event's no-registry workflow.

Tell the VM coding agent:

> Deploy this ZoneLogic application using the existing deploy-app-no-registry skill. Review the included prepare_deployment.py ConfigMap/runtime-image manifests (port 8080), inject the team's existing VSS environment through a Kubernetes Secret without printing it, and mount persistent storage at /data if available. Do not rebuild the organizers' ingestion pipeline. Run `python -m scripts.probe_workshop --location warehouse3`, verify the returned real clip and boxes, then check the deployed /app/ UI, source import, rule evaluation, and incident evidence. If persistence is unavailable, explicitly report that pod replacement will lose local incident history.

The app has no independent account system. Keep localhost binding for local work and use the event's access-controlled route for workshop use. Docker deployment should mount `/data` with write access for UID 10001. Deployment, GitHub publication, and submission are not implied by local tests; verify each separately.

To transfer this code to the VM, run `python -m scripts.package_project`. It creates `artifacts/ZoneLogic-workshop.zip`, containing source, tests and instructions, with no credentials or runtime data. Extract it on the VM and follow the setup above.

## Demo in 90 seconds

1. Open a real imported VAST segment and explain its provenance; use the clearly labeled simulation only if live access is unavailable.
2. Draw a protected passage, select an observed object class, and set a two-second dwell rule.
3. Play across the incident. Show the class, rule, video time, geometry/screenshot, and resolution action.
4. Change to the waste example and show an entry rule on a bottle. Explain that the classification rule is user-defined and says nothing about material composition.
5. Show that replay creates no duplicate ticket and export the event log.

## Contract references

- [Builders Challenge README](https://github.com/vast-data/vast-builders-challenge/blob/main/README.md)
- [Architecture and corpus reference](https://github.com/vast-data/vast-builders-challenge/blob/main/ARCHITECTURE_REFERENCE.md)
- [Official retrieval skills](https://github.com/vast-data/vast-builders-challenge/tree/main/.cursor/skills/retrieval)
- [GPU skills](https://github.com/vast-data/vast-builders-challenge/tree/main/.cursor/skills/gpu)
- [Deployment skill](https://github.com/vast-data/vast-builders-challenge/tree/main/.cursor/skills/deployment/deploy-app-no-registry)
