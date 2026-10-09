# Optional scenario playground

The original app still starts with `python run.py --port 8090`. Its files and
database are unchanged. The playground is a separate launcher that reuses the
existing rule engine and adds four generated icon scenes. No extra Python
dependencies are needed.

## Start on the workshop VM

After these new files have been committed and pushed, open another VM terminal:

```bash
cd ~/ZoneLogic-Tripwire
git pull --ff-only
.venv/bin/python run_scenarios.py --port 8091
```

Open **http://localhost:8091** in the browser inside the VM. Keep its terminal
open. The working app on port 8090 can keep running at the same time.

For VAST access in this new terminal, load your workshop configuration before
starting the playground:

```bash
cd ~/ZoneLogic-Tripwire
set -a
source /config/team-20.config
set +a
.venv/bin/python run_scenarios.py --port 8091
```

On Windows, from this project folder:

```powershell
python run_scenarios.py --port 8091
```

## Try the demos

Select a new source and press Play from the beginning. The starter zones and
rules are already saved. Each scene lasts 18 seconds.

| Source | Expected starter behavior |
| --- | --- |
| Recycling and trash | Bottle enters recycling: information log at 3.4s. Another bottle enters trash: review warning at 9.4s. Banana enters trash: information log at 15.4s. |
| Wheelchair route | A suitcase occupies the marked route for 2 seconds: warning at 4.8s. |
| Crosswalk vehicle | A car enters the marked pedestrian crossing: warning at 3.4s. |
| Cane camera cue | A car occupies the forward image region for 0.6 seconds: warning at 5.2s. |

To draw your own boundary, press **Draw zone**, name the region, click at least
three corners, and finish the zone. Select a rule, choose your new region in
**In this zone**, and press **Save rule**. Replay from the beginning. The scene
objects keep their scripted paths, so a region that they never enter correctly
produces no entry event. Select **Info** severity for ordinary placement
logs and **Warning** for placements you want reviewed.

Use the incident list to inspect the event and its evidence image. Replaying an
unchanged clip/rule combination does not duplicate an already recorded event.

## Optional vibration

Core rules always work without optional actions. For a saved rule, choose its
event meaning in the optional actions panel, enable its vibration preference,
and press **Save optional actions**. Enable device cues for the current page
before playback. Editing the core rule or its zone turns its previous optional
actions off until they are saved again.

The app always shows a visual event cue. Browser vibration is attempted only
when both switches are enabled and the browser supports it. Many desktop
browsers have no vibration hardware. Unsupported, rejected, or failed requests
leave playback and incident logging running. A browser accepting a request is
not proof that a physical motor vibrated. No walking-stick motor is connected.

## Other footage and places

The same polygons and class rules can be used with other VAST clips or an
uploaded recording plus its detection JSON. A new location does not require a
warehouse scene. Draw the boundary for that particular camera view. Detection
classes must actually exist in the supplied detections. Uploading video alone
does not run a local object detector.

The sorting scene uses author-chosen class assignments: bottle as a recycling
candidate and banana as a trash candidate. Those assignments can be changed;
they are not a material classifier or a statement about local recycling rules.
A geometric bin entry does not prove an item was thrown away.

The accessibility examples demonstrate image-region triggers. They do not
measure wheelchair clearance, distance, collision risk, or whether crossing is
safe. A moving cane camera would also need camera-motion handling and validated
sensing. These scenes use generated detections, not live camera inference.

## Isolation and fallback

- Playground data is under `data/scenarios/`, separate from the original `data/`
  database. Sources imported or zones drawn in one app do not automatically
  appear in the other.
- Missing optional settings leave device actions off. A failed optional action
  does not undo the core incident.
- If the optional pack cannot initialize, this launcher serves the original
  workspace with its separate storage.
- To explicitly bypass all additions, run
  `python run_scenarios.py --port 8091 --disable-scenarios`.
- To stop the playground, press Ctrl+C in its terminal. The original app stays
  available on port 8090.
- The existing Docker and packaging entry points remain unchanged and launch
  the original app. Use the new launcher explicitly to run these additions.

## Verification

```powershell
python -m pytest -q
node scripts/scenarios_smoke.cjs
```

The browser check requires Playwright and Microsoft Edge (or set
`PLAYWRIGHT_CHANNEL` to an installed Chromium channel). If Playwright is installed
outside this project, set `PLAYWRIGHT_MODULE` to its package directory. It uses
an isolated local server, browser profile, and data directory; it does not touch
the running application.
