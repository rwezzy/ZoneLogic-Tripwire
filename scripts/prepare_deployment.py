"""Prepare the workshop Kubernetes /app deployment without applying it.

Run from the repository root:
  python -m scripts.prepare_deployment --namespace team-17 --secret zonelogic-vss-creds

The existing Secret must provide VSS_URL, VSS_USERNAME and VSS_PASSWORD (optional
YOLO_URL and GPU_BEARER_TOKEN). This script never reads credentials, invokes
kubectl, creates a Secret, or changes the cluster. It writes a reviewable JSON
Kubernetes List containing code ConfigMap, Deployment, Service and Ingress.

The public Python image installs pinned requirements into a writable /deps volume
at startup. Application code is mounted read-only at /app; /data is an ephemeral
emptyDir unless --data-pvc names an existing team PVC. A code checksum triggers a
pod restart when a freshly generated manifest is subsequently applied.

After deployment, use https://workshop.thecosmoslabs.com and click App.
Reference: https://github.com/vast-data/vast-builders-challenge/blob/main/
.cursor/skills/deployment/deploy-app-no-registry/SKILL.md
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
from typing import Any


MAX_CONFIGMAP_BYTES = 900 * 1024
TEXT_ASSET_SUFFIXES = {".html", ".css", ".js", ".svg", ".json", ".txt"}
BINARY_ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".woff", ".woff2"}
EXCLUDED_PARTS = {"data", "reference", "tests", "node_modules", "__pycache__"}
START_COMMAND = (
    "set -eu\n"
    "python -m pip install --no-cache-dir --disable-pip-version-check --target=/deps -r /app/requirements.txt\n"
    "exec python /app/run.py --host 0.0.0.0 --port 8080\n"
)


class DeploymentError(ValueError):
    pass


def _dns_label(value: str, label: str, *, limit: int = 63) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= limit or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", value):
        raise DeploymentError(f"{label} must be a lowercase Kubernetes name of at most {limit} characters.")
    return value


def _dns_subdomain(value: str, label: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 253:
        raise DeploymentError(f"{label} must be a valid Kubernetes name.")
    for part in value.split("."):
        _dns_label(part, label)
    return value


def team_host(namespace: str, host: str | None = None) -> str:
    _dns_label(namespace, "Namespace")
    if not re.fullmatch(r"team-[1-9][0-9]*", namespace):
        raise DeploymentError("Workshop namespace must be your assigned team-N, for example team-17.")
    expected = f"video-lab-{namespace}.cosmos.vastdata.com"
    if host is not None and host != expected:
        raise DeploymentError(f"Workshop /app must use this team's existing host: {expected}")
    return expected


def collect_files(root: Path) -> list[tuple[str, bytes]]:
    """Allowlist executable code and static assets; never bundle runtime data."""
    root = root.resolve()
    candidates = [root / "requirements.txt", root / "run.py"]
    package = root / "zonelogic"
    if not package.is_dir():
        raise DeploymentError("The source root must contain the zonelogic package.")
    candidates.extend(package.rglob("*.py"))
    static = package / "static"
    if static.is_dir():
        candidates.extend(p for p in static.rglob("*")
                          if p.suffix.lower() in TEXT_ASSET_SUFFIXES | BINARY_ASSET_SUFFIXES)
    files = []
    for path in sorted(set(candidates), key=lambda p: p.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if any(part.startswith(".") or part in EXCLUDED_PARTS for part in relative.parts):
            continue
        # Refuse even in-tree symlinks: ConfigMap content must be the selected
        # application's own bytes, with no link accidentally capturing a secret.
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise DeploymentError(f"Do not bundle symlinks or files outside the project: {relative.as_posix()}")
        if not path.is_file():
            raise DeploymentError(f"Required application file is missing: {relative.as_posix()}")
        if path.stat().st_size >= MAX_CONFIGMAP_BYTES:
            raise DeploymentError(f"Application asset exceeds the 900 KiB ConfigMap budget: {relative.as_posix()}")
        files.append((relative.as_posix(), path.read_bytes()))
    required = {"run.py", "requirements.txt", "zonelogic/__init__.py", "zonelogic/main.py", "zonelogic/static/index.html"}
    if missing := required - {name for name, _ in files}:
        raise DeploymentError("Required application files are missing: " + ", ".join(sorted(missing)))
    return files


def build_manifest(
    root: Path, *, namespace: str, secret: str, host: str | None = None,
    name: str = "zonelogic", data_pvc: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    host = team_host(namespace, host)
    _dns_label(name, "App name", limit=58)  # leave space for the -code suffix
    _dns_subdomain(secret, "Existing Secret name")
    if data_pvc:
        _dns_subdomain(data_pvc, "Existing data PVC name")
    files = collect_files(root)
    labels = {"app": name, "app.kubernetes.io/name": name, "team": namespace}

    def metadata(resource_name: str) -> dict:
        return {"name": resource_name, "namespace": namespace, "labels": dict(labels)}

    data, binary_data, items = {}, {}, []
    for index, (path, contents) in enumerate(files):
        key = f"file-{index:04d}"
        items.append({"key": key, "path": path})
        if Path(path).suffix.lower() in BINARY_ASSET_SUFFIXES:
            binary_data[key] = base64.b64encode(contents).decode("ascii")
        else:
            try:
                data[key] = contents.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise DeploymentError(f"Application text must be UTF-8: {path}") from exc
    configmap = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": metadata(name + "-code"), "data": data}
    if binary_data:
        configmap["binaryData"] = binary_data
    configmap_bytes = len(json.dumps(configmap, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if configmap_bytes >= MAX_CONFIGMAP_BYTES:
        raise DeploymentError(f"Code ConfigMap is {configmap_bytes:,} bytes; keep it below {MAX_CONFIGMAP_BYTES:,} bytes (900 KiB). Remove large assets; videos, models and dependencies must not be bundled.")
    digest = hashlib.sha256()
    for path, contents in files:
        digest.update(path.encode("utf-8") + b"\0" + contents + b"\0")
    code_hash = digest.hexdigest()
    data_volume = {"persistentVolumeClaim": {"claimName": data_pvc}} if data_pvc else {"emptyDir": {}}
    deployment = {
        "apiVersion": "apps/v1", "kind": "Deployment", "metadata": metadata(name),
        "spec": {
            "replicas": 1, "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"app": name}},
            "template": {
                "metadata": {"labels": dict(labels), "annotations": {"checksum/code": code_hash}},
                "spec": {
                    "securityContext": {"runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000, "fsGroup": 1000},
                    "containers": [{
                        "name": "app", "image": "python:3.12-slim", "imagePullPolicy": "IfNotPresent",
                        "workingDir": "/app", "command": ["/bin/sh", "-c"], "args": [START_COMMAND],
                        "ports": [{"name": "http", "containerPort": 8080}],
                        "envFrom": [{"secretRef": {"name": secret}}],
                        "env": [{"name": key, "value": value} for key, value in {
                            "PORT": "8080", "HOST": "0.0.0.0", "ZONELOGIC_DATA_DIR": "/data",
                            "PYTHONPATH": "/deps:/app", "PYTHONDONTWRITEBYTECODE": "1",
                            "PYTHONUNBUFFERED": "1", "HOME": "/tmp",
                        }.items()],
                        "volumeMounts": [
                            {"name": "code", "mountPath": "/app", "readOnly": True},
                            {"name": "dependencies", "mountPath": "/deps"},
                            {"name": "data", "mountPath": "/data"},
                            {"name": "temporary", "mountPath": "/tmp"},
                        ],
                        "startupProbe": {"httpGet": {"path": "/health", "port": "http"}, "periodSeconds": 5, "failureThreshold": 60},
                        "readinessProbe": {"httpGet": {"path": "/health", "port": "http"}, "periodSeconds": 10},
                        "resources": {"requests": {"cpu": "100m", "memory": "256Mi"},
                                      "limits": {"cpu": "1", "memory": "1Gi"}},
                        "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                            "capabilities": {"drop": ["ALL"]}},
                    }],
                    "volumes": [
                        {"name": "code", "configMap": {"name": name + "-code", "items": items, "defaultMode": 292}},
                        {"name": "dependencies", "emptyDir": {}},
                        {"name": "data", **data_volume},
                        {"name": "temporary", "emptyDir": {}},
                    ],
                },
            },
        },
    }
    service = {"apiVersion": "v1", "kind": "Service", "metadata": metadata(name),
               "spec": {"selector": {"app": name}, "type": "ClusterIP",
                        "ports": [{"name": "http", "port": 80, "targetPort": "http"}]}}
    ingress = {"apiVersion": "networking.k8s.io/v1", "kind": "Ingress", "metadata": metadata(name),
               "spec": {"ingressClassName": "nginx", "rules": [{"host": host, "http": {"paths": [
                   {"path": "/app(/|$)(.*)", "pathType": "ImplementationSpecific",
                    "backend": {"service": {"name": name, "port": {"number": 80}}}}
               ]}}]}}
    ingress["metadata"]["annotations"] = {
        "nginx.ingress.kubernetes.io/rewrite-target": "/$2",
        "nginx.ingress.kubernetes.io/use-regex": "true",
        "nginx.ingress.kubernetes.io/proxy-body-size": "110m",
        "nginx.ingress.kubernetes.io/proxy-read-timeout": "240",
    }
    manifest = {"apiVersion": "v1", "kind": "List", "items": [configmap, deployment, service, ingress]}
    summary = {"namespace": namespace, "host": host, "name": name, "secret": secret,
               "file_count": len(files), "code_bytes": sum(len(raw) for _, raw in files),
               "configmap_bytes": configmap_bytes, "code_sha256": code_hash,
               "data_storage": f"existing PVC {data_pvc}" if data_pvc else "ephemeral emptyDir (lost on pod replacement)",
               "files": [path for path, _ in files]}
    return manifest, summary


def deployment_instructions(summary: dict[str, Any]) -> str:
    ns, name, secret = summary["namespace"], summary["name"], summary["secret"]
    return f"""ZoneLogic workshop deployment — prepared only; nothing has been applied.

Namespace: {ns}
Ingress: {summary['host']}/app
Existing Secret: {secret}
Data storage: {summary['data_storage']}
Files: {summary['file_count']}; ConfigMap bytes: {summary['configmap_bytes']:,}

Review manifest.json and package-summary.json first. Only use your assigned team's
VM/configuration and namespace. The runtime must be able to pull python:3.12-slim
and install requirements from the Python package index. No image build is needed.

On the team's Linux workshop VM, load its configuration (do not print the file):
  export KUBECONFIG=/config/kubeconfig
  set -a
  source /config/{ns}.config
  set +a

The kubeconfig may instead be /config/{ns}-k8s.yaml. Use the provided file.
The Secret must contain VSS_URL, VSS_USERNAME, VSS_PASSWORD; optional YOLO_URL and
GPU_BEARER_TOKEN. A backend Secret with differently named keys is not compatible.
If this app Secret does not exist, the official setup is:
  kubectl -n {ns} create secret generic {secret} \\
    --from-literal=VSS_URL="$INGRESS_URL" \\
    --from-literal=VSS_USERNAME="$USERNAME" \\
    --from-literal=VSS_PASSWORD="$PASSWORD" \\
    --dry-run=client -o yaml | kubectl apply -f -
Never redirect that Secret YAML into a repository or run with shell tracing.

From this generated output directory, after reviewing the manifest:
  kubectl -n {ns} apply -f manifest.json
  kubectl -n {ns} rollout status deployment/{name}
  kubectl -n {ns} get pods,svc,ingress -l app={name}
  curl -f http://{summary['host']}/app/health

Open https://workshop.thecosmoslabs.com and click App. The /app route is the
team's single app route; resolve any existing conflicting app Ingress first.
VSS service credentials do not add end-user authentication to these app routes;
use the workshop gateway's access controls for the team demo.

Regenerate this manifest after code edits and apply the new file. Its code
checksum changes the pod template so Kubernetes restarts the app. If only Secret
values change, explicitly restart deployment/{name} after updating the Secret.
With default emptyDir storage, pod replacement loses uploaded media, zones and
incident history. Use --data-pvc with an existing writable team PVC to retain it.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--namespace", required=True, help="Your assigned workshop team-N namespace")
    parser.add_argument("--secret", required=True, help="Existing Kubernetes Secret name containing VSS_* environment variables")
    parser.add_argument("--host", help="Optional confirmation of the exact video-lab-team-N.cosmos.vastdata.com host")
    parser.add_argument("--name", default="zonelogic", help="Kubernetes resource prefix (default: zonelogic)")
    parser.add_argument("--output", type=Path, default=Path("artifacts/deployment"), help="Directory for reviewable JSON manifests")
    parser.add_argument("--data-pvc", help="Existing writable team PVC for persistent /data; default is ephemeral")
    args = parser.parse_args(argv)
    try:
        manifest, summary = build_manifest(Path(__file__).resolve().parents[1], namespace=args.namespace,
            secret=args.secret, host=args.host, name=args.name, data_pvc=args.data_pvc)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (args.output / "package-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        (args.output / "DEPLOY.txt").write_text(deployment_instructions(summary), encoding="utf-8")
    except (DeploymentError, OSError) as exc:
        parser.exit(2, f"Deployment preparation failed: {exc}\n")
    print(f"Prepared {summary['file_count']} files; ConfigMap {summary['configmap_bytes']:,} bytes of the {MAX_CONFIGMAP_BYTES:,}-byte budget.")
    print(f"Review: {(args.output / 'manifest.json').resolve()}")
    print(f"Instructions: {(args.output / 'DEPLOY.txt').resolve()}")
    print(f"Storage: {summary['data_storage']}.")
    print("No cluster changes were made and no credentials were included.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
