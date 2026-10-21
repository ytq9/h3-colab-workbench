import ast
import json
from pathlib import Path
import sys
import tempfile
import unittest

from h3_workbench.config import RESOURCES, Settings, load_settings, save_settings
from h3_workbench.worker import job_script, lease_script, setup_script


class ConfigurationTests(unittest.TestCase):
    def test_wrong_account_reconnect_restores_previous_credentials(self):
        from h3_workbench.colab_cli import accept_identity
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "previous-token.json").write_text("previous-fixture")
            (root / "token.json").write_text("wrong-account-fixture")
            self.assertFalse(accept_identity(root, "other@example.com", "you@example.com", True))
            self.assertEqual((root / "token.json").read_text(), "previous-fixture")
            self.assertFalse((root / "identity.json").exists())

    def test_round_trip_keeps_venv_python_instead_of_resolving_its_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            python = root / "venv-python"
            python.symlink_to(sys.executable)
            original = Settings("you@example.com", root / "project", root / "runtime", root / "private", python)
            path = save_settings(original, root / "config.json")
            self.assertEqual(load_settings(path), original)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_credentials_cannot_be_stored_under_project(self):
        settings = Settings("you@example.com", Path("/project"), Path("/runtime"), Path("/project/private"), Path("/python"))
        with self.assertRaises(ValueError):
            settings.validate()

    def test_worker_scripts_are_self_contained_and_compile(self):
        for script in (setup_script(), job_script("/content/request.json", "/content/result.json"), lease_script(180)):
            compile(script, "remote-script", "exec")
            self.assertNotIn("drive.mount(", script)
            self.assertNotIn("cloudflared", script)
        with self.assertRaises(ValueError):
            lease_script(3600)

    def test_transfer_lease_prevents_idle_release_but_has_a_bound(self):
        tree = ast.parse((RESOURCES / "worker_bootstrap.py").read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "should_release")
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "guard-policy", "exec"), namespace)
        release = namespace["should_release"]
        self.assertFalse(release(240, 0, False, 0, 300))
        self.assertTrue(release(301, 0, False, 0, 300))
        self.assertFalse(release(301, 0, True, 0, 0))
        self.assertTrue(release(2701, 0, True, 2700, 3000))

    def test_manifest_workflows_are_present_and_readable(self):
        manifest = json.loads((RESOURCES / "manifest.json").read_text())
        self.assertEqual(sum(m["size"] for m in manifest["models"]), 42030035159)
        for workflow in manifest["workflows"]:
            data = json.loads((RESOURCES / "workflows" / workflow["name"]).read_text())
            self.assertIsInstance(data["nodes"], list)
            self.assertTrue(data["nodes"])
