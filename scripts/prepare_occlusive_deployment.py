"""Prepare the optional branded app using the original tested deployment builder."""
import argparse
import json
from pathlib import Path
from scripts import prepare_deployment as deployment


def build(root, namespace="team-20", secret="occlusive-vss-creds", data_pvc=None):
    original = deployment.collect_files
    def collect(directory):
        files = [(name, data) for name, data in original(directory) if name != "run.py"]
        files.append(("run.py", (directory / "run_occlusive.py").read_bytes()))
        for path in sorted((directory / "occlusive").rglob("*")):
            if path.is_file() and path.suffix in {".py", ".js", ".css"} and "__pycache__" not in path.parts:
                if path.is_symlink():
                    raise ValueError("Do not package symbolic links")
                files.append((path.relative_to(directory).as_posix(), path.read_bytes()))
        return files
    deployment.collect_files = collect
    try:
        manifest, summary = deployment.build_manifest(root, namespace=namespace, secret=secret, name="occlusive-logic", data_pvc=data_pvc)
    finally:
        deployment.collect_files = original
    container = manifest["items"][1]["spec"]["template"]["spec"]["containers"][0]
    container["env"].append({"name": "OCCLUSIVE_DATA_DIR", "value": "/data/occlusive"})
    return manifest, summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default="team-20")
    parser.add_argument("--secret", default="occlusive-vss-creds")
    parser.add_argument("--data-pvc")
    args = parser.parse_args()
    manifest, summary = build(Path(__file__).resolve().parents[1], args.namespace, args.secret, args.data_pvc)
    directory = Path("artifacts/occlusive-deployment"); directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (directory / "package-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (directory / "DEPLOY.txt").write_text(deployment.deployment_instructions(summary), encoding="utf-8")
    print("Prepared only; no cluster changes:", directory.resolve())
    print("Code bytes:", summary["configmap_bytes"], "Storage:", summary["data_storage"])
    print("Inspect existing /app ingress before applying; do not overwrite another working app route blindly.")
