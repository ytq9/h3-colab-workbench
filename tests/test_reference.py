import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from h3_workbench.catalog import plan_environment, reference_catalog
from h3_workbench.worker import setup_script
from test_broker import FakeBroker, PROMPT


class ReferenceWorkflowTests(unittest.TestCase):
    def test_selected_models_and_plugins_exclude_unreachable_branches(self):
        models = reference_catalog()["models"]
        model = next(m for m in models if m["destination"].startswith("diffusion_models/"))
        tiny = next(m for m in models if m["destination"].startswith("vae_approx/"))
        prompt = {
            "1": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {}},
            "2": {"class_type": "UNETLoader", "inputs": {"unet_name": model["filename"]}},
            "3": {"class_type": "ModelPreviewOverrideKJ", "inputs": {"model": ["2", 0], "tiny_vae": "taeh3.safetensors"}},
            "4": {"class_type": "BasicGuider", "inputs": {"model": ["3", 0], "conditioning": ["1", 0]}},
            "5": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["4", 0]}},
            "unused": {"class_type": "UNETLoader", "inputs": {"unet_name": "not-installed.safetensors"}},
        }
        plan = plan_environment(prompt, ["5"])
        self.assertEqual({m["destination"] for m in plan["models"]}, {model["destination"], tiny["destination"]})
        self.assertEqual({e["name"] for e in plan["extensions"]}, {"h3_KJNodes", "h3_VideoHelperSuite"})
        compile(setup_script(plan), "reference-setup", "exec")

    def test_unknown_model_is_rejected_before_any_cloud_call(self):
        with tempfile.TemporaryDirectory() as root:
            broker = FakeBroker(root)
            prompt = dict(PROMPT, loader={"class_type": "UNETLoader", "inputs": {"unet_name": "unknown"}})
            with self.assertRaisesRegex(ValueError, "下载来源"):
                broker.execute(prompt, str(uuid.uuid4()), {}, lambda: False)
            self.assertEqual(broker.calls, [])

    def test_reference_media_are_snapshotted_using_each_original_input_type(self):
        with tempfile.TemporaryDirectory() as root:
            broker = FakeBroker(root)
            inputs = broker.project / "input"
            inputs.mkdir(parents=True)
            prompt = {"1": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {}}}
            for i, (kind, field, name) in enumerate([
                ("LoadImage", "image", "still.png"), ("LoadAudio", "audio", "voice.wav"),
                ("VHS_LoadVideo", "video", "motion.mp4"),
            ], start=2):
                (inputs / name).write_bytes(b"fixture")
                prompt[str(i)] = {"class_type": kind, "inputs": {field: name}}
            _, request, assets = broker.prepare_job(prompt, str(uuid.uuid4()), {})
            data = json.loads(request.read_text())
            self.assertEqual(len(assets), 3)
            for key, field in (("2", "image"), ("3", "audio"), ("4", "video")):
                self.assertTrue(data["prompt"][key]["inputs"][field].startswith(data["prompt_id"]))
            self.assertEqual(prompt["3"]["inputs"]["audio"], "voice.wav")
            self.assertEqual(broker.calls, [])
            prompt["3"]["inputs"]["audio"] = "../private.wav"
            with self.assertRaises(ValueError):
                broker.prepare_job(prompt, str(uuid.uuid4()), {})

    def test_different_model_batches_release_before_reallocation(self):
        with tempfile.TemporaryDirectory() as root:
            broker = FakeBroker(root)
            broker.execute(PROMPT, str(uuid.uuid4()), {}, lambda: True)
            broker.execute({"1": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {}}},
                           str(uuid.uuid4()), {}, lambda: False)
            lifecycle = [c[0] for c in broker.calls if c[0] in {"new", "stop"}]
            self.assertEqual(lifecycle, ["new", "stop", "new", "stop"])

    def test_live_events_are_bound_to_this_job_and_browser(self):
        with tempfile.TemporaryDirectory() as root:
            broker = FakeBroker(root)
            broker.event_job_id = "current"
            broker.event_client_id = "browser"
            broker.emit = Mock()
            event = {"type": "kj_preview_override", "job_id": "current", "data": {"node_id": "150"}}
            broker.relay_event(json.dumps(event))
            broker.emit.assert_called_once_with(event["type"], event["data"], "browser")
            for changed in (dict(event, job_id="other"), dict(event, type="execution_error"),
                            dict(event, data={"prompt_id": "other"})):
                broker.relay_event(json.dumps(changed))
            self.assertEqual(broker.emit.call_count, 1)
            broker.emit.side_effect = RuntimeError("browser closed")
            broker.relay_event(json.dumps(event))

    def test_setup_without_ready_marker_releases_before_upload(self):
        with tempfile.TemporaryDirectory() as root:
            broker = FakeBroker(root)
            original = broker.command
            def no_ready(args, **kwargs):
                result = original(args, **kwargs)
                return "setup failed" if args[0] == "exec" else result
            with patch.object(broker, "command", no_ready):
                with self.assertRaisesRegex(RuntimeError, "未成功就绪"):
                    broker.execute(PROMPT, str(uuid.uuid4()), {}, lambda: False)
            self.assertNotIn("upload", [c[0] for c in broker.calls])
            self.assertTrue(broker.release_confirmed)

    def test_vhs_video_and_workflow_sidecar_return_to_local_project(self):
        with tempfile.TemporaryDirectory() as root:
            broker = FakeBroker(root)
            folder = broker.project / "jobs" / str(uuid.uuid4())
            folder.mkdir(parents=True)
            result = {"outputs": {"1": {"gifs": [{
                "filename": "clip.mp4", "subfolder": "clips", "type": "output",
                "format": "video/h264-mp4", "workflow": "clip.png", "fullpath": "/remote/clip.mp4",
            }]}}, "files": [{"filename": name, "subfolder": "clips", "size": 7,
                            "remote_path": "/content/h3-worker-files/output/clips/" + name}
                           for name in ("clip.mp4", "clip.png")]}
            restored = broker.retrieve_result(folder, result)["outputs"]["1"]["gifs"][0]
            self.assertEqual(restored["fullpath"], str(broker.project / "output" / restored["subfolder"] / "clip.mp4"))
            self.assertTrue(Path(restored["fullpath"]).is_file())
            self.assertTrue((Path(restored["fullpath"]).parent / restored["workflow"]).is_file())
