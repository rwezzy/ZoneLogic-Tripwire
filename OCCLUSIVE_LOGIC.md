# Occlusive Logic: compound Boolean rules

This is a separate optional application. The original `run.py`, RuleEngine,
frontend files, VAST client, saved configuration, and deployment preparer have
not been edited. The repository name remains ZoneLogic-Tripwire; the new app's
visible brand is Occlusive Logic. No new dependencies or model training.

## First: protect the submission

The existing demonstration recording has been copied to
`artifacts/submission/ZoneLogic-working-demo.mp4` on the development computer.
It demonstrates the previous single-object workflow, not compound verification.
It still needs a shareable video link for submission. Local saving is not upload.

Deployment and submission have not been confirmed. The organizer's submission
page supplied in the brief is https://tokensand.com/vastnyc/submit . Use the
existing working app/video as the fallback if the new feature cannot be verified
before the deadline. Do not let the optional feature delay submitting.

## Start separately on the workshop VM

After committing/pushing these additions from Windows, use a new VM terminal:

```bash
cd ~/ZoneLogic-Tripwire
git pull --ff-only
set -a
source /config/team-20.config
set +a
.venv/bin/python -m scripts.copy_occlusive_workspace --source data --destination data/occlusive
.venv/bin/python run_occlusive.py --port 8092
```

The copy command snapshots SQLite and copies cached video files. It refuses to
overwrite an existing destination. If `data/occlusive` already exists, simply
start the launcher to resume it. If there is no original `data/zonelogic.sqlite3`,
skip the copy and start with a fresh workspace, then import a VAST clip.

Open http://localhost:8092 inside the VM. Your original app stays on its current
port. The new default data directory is `data/occlusive`.

On Windows, use `python run_occlusive.py --port 8092`. Stop only that terminal
with Ctrl+C to return to the original app.

## Configure a real example

1. Import/select a real VAST segment with actual `person` and `car` labels, or
   `person` and `truck`. Do not substitute a made-up forklift class.
2. Draw and save a camera-specific zone, such as the visible crosswalk.
3. In **Compound Boolean Rules**, choose `person`, that zone, and a confidence
   threshold for A. Choose `car`, the same zone, and a threshold for B.
4. Select AND; start with dwell 0 and cooldown 5 seconds. Save the compound rule.
5. Play from the beginning. Both class predicates are evaluated on each identical
   normalized detection frame, using each box's center inside its selected zone.
6. Watch the A/B/result indicator. Inspect the generated incident, screenshot,
   and video. Check a frame where the expression is false as well as one true.

Each input can use a different zone. The dropdown only offers class labels
observed in the selected source. Add another compound rule with the **New
compound rule** picker entry. Existing single-object rules remain separate.

## Exact logic

| A | B | AND | OR | XOR |
|---|---|-----|----|-----|
| 0 | 0 | 0 | 0 | 0 |
| 0 | 1 | 0 | 1 | 1 |
| 1 | 0 | 0 | 1 | 1 |
| 1 | 1 | 1 | 1 | 0 |

- A raw input is true when **any** matching class detection meets its confidence
  threshold and its box center lies inside/on the zone boundary. No tracking-ID
  continuity or cross-frame memory is used to combine A and B.
- NOT reverses that detection-presence Boolean on an observed frame. It does not
  establish that an object is physically absent. A missed detection can change
  NOT and XOR results; use them with appropriate caution and human review.
- Only an observed false-to-true transition can arm an event. If the first frame
  is already true, the indicator shows 1 but no transition incident is invented.
- Optional dwell requires the expression to stay true through successive
  observations. A false frame clears pending dwell. When satisfied, one incident
  is emitted for that truth episode.
- Cooldown is per compound rule. A qualifying episode within cooldown is
  suppressed, not delayed until the middle of an already-true episode.
- Seeks, backward time, and detection gaps greater than two seconds reset the
  temporal baseline. Repeated timestamps do not accumulate dwell. A held display
  becomes unknown (`?`) after two seconds without a fresh detection frame.
- Missing/deleted zones and disabled rules produce unknown states and no event.
- Changing rules requires a new playback session. Incident IDs include source,
  original config, compound config, and event evidence, so unchanged replays do
  not duplicate tickets or undo resolution.
- SQLite incidents, screenshot capture/validation, export, and resolution reuse
  the original implementation. A screenshot is only saved when playback is close
  enough to the event time; otherwise the incident retains detection geometry.

This is a Boolean detection-condition engine for review. AND over person/car
can indicate potential co-occupancy; it does not estimate collision risk, speed,
distance, or safe crossing. Normalization and VAST retrieval are unchanged.

## Verify on VAST without modifying data

After saving the real rule in the new app:

```bash
cd ~/ZoneLogic-Tripwire
.venv/bin/python -m scripts.verify_compound_vast --data-dir data/occlusive
```

The report is `artifacts/submission/compound-vast-verification.json`. A pass
requires an imported VAST source with cached video, an un-negated AND rule,
positive and negative same-frame observations, and at least one valid transition
event. Review the reported timestamps visually. This tool never labels built-in
simulations or uploaded test fixtures as real VAST verification.

The development machine currently has no imported VAST sources or credentials.
Its report therefore says **NOT VERIFIED**. Automated fixtures are not a live
VAST verification claim.

## Deployment

Check existing resources first; the workshop permits one app at its `/app` route:

```bash
export KUBECONFIG=/config/kubeconfig
kubectl -n team-20 get deployments,services,ingresses,pvc
```

The original deployment preparer is unchanged. To prepare the new branded build:

```bash
cd ~/ZoneLogic-Tripwire
.venv/bin/python -m scripts.prepare_occlusive_deployment
```

Review `artifacts/occlusive-deployment/manifest.json` and `DEPLOY.txt`.
The new launcher and package are included; the original builder's ConfigMap,
Service, runtime image, and `/app` ingress contracts are reused. The new data
directory is `/data/occlusive`. Use `--data-pvc EXISTING_CLAIM` if available;
without it, pod replacement loses that pod's local data. VM-local data does not
automatically appear in the Kubernetes pod.

Do not apply a second conflicting `/app` ingress over another working app. Once
the route is confirmed free, the generated DEPLOY.txt contains the exact Secret,
apply, and rollout commands. These preparation scripts do not deploy anything.
Open the workshop portal's **App** button after the rollout is healthy.

## Verification commands

```powershell
python -m pytest -q
node scripts/compound_smoke.cjs
```

The browser script uses Playwright (set PLAYWRIGHT_MODULE if installed elsewhere)
and Edge by default. It decodes a clearly labeled synthetic WebM fixture, saves a
compound rule through the UI, checks positive/negative frames, screenshot evidence,
resolution, replay deduplication, and mobile overflow. It does not claim real VAST.
