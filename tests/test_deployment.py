"""Verify the prepared workshop manifest without a Kubernetes cluster."""
import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.prepare_deployment import MAX_CONFIGMAP_BYTES, DeploymentError, build_manifest, collect_files, team_host


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for relative, value in {
            "run.py": "print('run')\n", "requirements.txt": "fastapi==0.116.1\n",
            "zonelogic/__init__.py": "", "zonelogic/main.py": "print('server')\n",
            "zonelogic/static/index.html": "<!doctype html><title>ZoneLogic</title>",
            "zonelogic/static/css/main.css": "body { color: black; }",
        }.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def build(self, **kwargs):
        return build_manifest(self.root, namespace="team-17", secret="zonelogic-vss-creds", **kwargs)

    def test_preserves_package_tree_and_binary_assets(self):
        (self.root / "zonelogic/static/icon.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        manifest, summary = self.build()
        config, deploy, service, ingress = manifest["items"]
        code = deploy["spec"]["template"]["spec"]["volumes"][0]["configMap"]
        paths = {entry["path"]: entry["key"] for entry in code["items"]}
        self.assertIn("zonelogic/static/css/main.css", paths)
        self.assertEqual(config["data"][paths["zonelogic/main.py"]], (self.root / "zonelogic/main.py").read_bytes().decode("utf-8"))
        self.assertEqual(base64.b64decode(config["binaryData"][paths["zonelogic/static/icon.png"]]), b"\x89PNG\r\n\x1a\n")
        self.assertLess(summary["configmap_bytes"], MAX_CONFIGMAP_BYTES)
        self.assertEqual(summary["file_count"], 7)

    def test_never_packages_environment_data_reference_or_dependencies(self):
        for relative in [".env", "data/incident.json", "reference/example.py", "node_modules/a.js",
                         "zonelogic/.env", "zonelogic/__pycache__/compiled.py",
                         "zonelogic/static/.secret.json", "zonelogic/static/data/incident.json"]:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("sensitive-excluded-sentinel", encoding="utf-8")
        with patch.dict("os.environ", {"VSS_PASSWORD": "environment-password-sentinel", "GPU_BEARER_TOKEN": "environment-token-sentinel"}):
            manifest, summary = self.build()
        encoded = json.dumps(manifest)
        for forbidden in ["sensitive-excluded-sentinel", "environment-password-sentinel", "environment-token-sentinel"]:
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(len(manifest["items"]), 4)
        self.assertNotIn("Secret", [item["kind"] for item in manifest["items"]])

    def test_correct_workshop_route_service_and_health(self):
        manifest, _ = self.build()
        _, deployment, service, ingress = manifest["items"]
        for resource in manifest["items"]:
            self.assertEqual(resource["metadata"]["namespace"], "team-17")
            self.assertEqual(resource["metadata"]["labels"]["team"], "team-17")
        self.assertEqual(ingress["spec"]["rules"][0]["host"], "video-lab-team-17.cosmos.vastdata.com")
        self.assertEqual(ingress["spec"]["rules"][0]["http"]["paths"][0]["path"], "/app(/|$)(.*)")
        self.assertEqual(ingress["metadata"]["annotations"]["nginx.ingress.kubernetes.io/rewrite-target"], "/$2")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["readinessProbe"]["httpGet"]["path"], "/health")
        self.assertEqual(container["envFrom"], [{"secretRef": {"name": "zonelogic-vss-creds"}}])
        self.assertIn("--target=/deps", container["args"][0])
        self.assertIn("--host 0.0.0.0 --port 8080", container["args"][0])
        self.assertTrue(container["volumeMounts"][0]["readOnly"])
        self.assertEqual(service["spec"]["ports"][0]["port"], 80)

    def test_emptydir_default_and_optional_existing_pvc(self):
        manifest, summary = self.build()
        volumes = manifest["items"][1]["spec"]["template"]["spec"]["volumes"]
        self.assertEqual(next(v for v in volumes if v["name"] == "data"), {"name": "data", "emptyDir": {}})
        self.assertIn("lost", summary["data_storage"])
        manifest, summary = self.build(data_pvc="zone-data")
        volumes = manifest["items"][1]["spec"]["template"]["spec"]["volumes"]
        self.assertEqual(next(v for v in volumes if v["name"] == "data")["persistentVolumeClaim"]["claimName"], "zone-data")
        self.assertEqual(manifest["items"][1]["spec"]["strategy"]["type"], "Recreate")

    def test_exact_team_host_and_safe_names_are_required(self):
        self.assertEqual(team_host("team-17"), "video-lab-team-17.cosmos.vastdata.com")
        for namespace, host in [("default", None), ("team-17;whoami", None), ("team-17", "video-lab-team-18.cosmos.vastdata.com"),
                                ("team-17", "https://video-lab-team-17.cosmos.vastdata.com")]:
            with self.subTest(namespace=namespace, host=host), self.assertRaises(DeploymentError):
                team_host(namespace, host)
        with self.assertRaises(DeploymentError):
            build_manifest(self.root, namespace="team-17", secret="creds;echo nope")

    def test_large_code_is_rejected_before_output(self):
        (self.root / "zonelogic/static/large.js").write_text("a" * MAX_CONFIGMAP_BYTES, encoding="utf-8")
        with self.assertRaisesRegex(DeploymentError, "900 KiB"):
            self.build()

    def test_new_code_changes_pod_checksum(self):
        first, _ = self.build()
        same, _ = self.build()
        annotation = lambda doc: doc["items"][1]["spec"]["template"]["metadata"]["annotations"]["checksum/code"]
        self.assertEqual(annotation(first), annotation(same))
        (self.root / "zonelogic/main.py").write_text("print('changed')\n", encoding="utf-8")
        updated, _ = self.build()
        self.assertNotEqual(annotation(first), annotation(updated))

    def test_missing_entrypoint_cannot_make_deployable_manifest(self):
        (self.root / "run.py").unlink()
        with self.assertRaisesRegex(DeploymentError, "missing"):
            collect_files(self.root)


if __name__ == "__main__":
    unittest.main()
