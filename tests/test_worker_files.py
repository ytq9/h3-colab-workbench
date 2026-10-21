"""File metadata paths must not scan model/video contents; no Colab required."""
import ast
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from h3_workbench.config import RESOURCES


class WorkerFileTests(unittest.TestCase):
    def test_worker_collects_vhs_sidecar_even_when_live_preview_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            output = root / "output/clips"
            output.mkdir(parents=True)
            for name in ("clip.mp4", "clip.png"):
                (output / name).write_bytes(b"fixture")
            request, result = root / "request.json", root / "result.json"
            request.write_text(json.dumps({"prompt": {}, "client_id": "fixture", "stream_events": True}))
            namespace = {"OUTPUT_DIR": output.parent}
            exec((RESOURCES / "worker_job.py").read_text(), namespace)
            namespace["h3_start_events"] = Mock(side_effect=OSError("preview offline"))
            namespace["h3_job_json"] = Mock(side_effect=[{"prompt_id": "fixture"}, {"fixture": {
                "status": {"status_str": "success"}, "outputs": {"1": {"gifs": [{
                    "filename": "clip.mp4", "subfolder": "clips", "type": "output", "workflow": "clip.png",
                }]}}
            }}])
            with redirect_stdout(io.StringIO()):
                namespace["h3_worker_job"](str(request), str(result))
            receipt = json.loads(result.read_text())
            self.assertTrue(receipt["success"], receipt.get("error"))
            self.assertEqual({f["filename"] for f in receipt["files"]}, {"clip.mp4", "clip.png"})
            self.assertNotIn("stream_events", namespace["h3_job_json"].call_args_list[0].args[1])
            self.assertFalse(namespace["H3_WORKER_BUSY"])
            (output / "clip.png").unlink()
            namespace["h3_job_json"].side_effect = [
                {"prompt_id": "fixture"}, {"fixture": {"status": {"status_str": "success"},
                "outputs": receipt["outputs"]}},
            ]
            with redirect_stdout(io.StringIO()):
                namespace["h3_worker_job"](str(request), str(result))
            receipt = json.loads(result.read_text())
            self.assertTrue(receipt["success"], receipt.get("error"))
            self.assertEqual([f["filename"] for f in receipt["files"]], ["clip.mp4"])
            self.assertNotIn("workflow", receipt["outputs"]["1"]["gifs"][0])

    def test_worker_event_relay_filters_other_jobs_and_closes_connection(self):
        namespace = {}
        exec((RESOURCES / "worker_job.py").read_text(), namespace)
        events = [
            {"type": "status", "data": {}},
            {"type": "progress", "data": {"prompt_id": "another", "value": 1}},
            {"type": "progress", "data": {"prompt_id": "fixture", "value": 1}},
            {"type": "kj_preview_override", "data": {"node_id": "150", "image": "fixture"}},
        ]
        messages = iter([b"binary"] + [json.dumps(event) for event in events])
        drained = threading.Event()
        def receive():
            try:
                return next(messages)
            except StopIteration:
                drained.set()
                raise OSError("closed")
        connection = Mock(recv=Mock(side_effect=receive))
        websocket = SimpleNamespace(create_connection=Mock(return_value=connection),
                                    WebSocketTimeoutException=TimeoutError)
        captured = io.StringIO()
        with patch.dict(sys.modules, {"websocket": websocket}), redirect_stdout(captured):
            stop = namespace["h3_start_events"]("fixture")
            self.assertTrue(drained.wait(2))
            stop()
        relayed = [json.loads(line.removeprefix("H3_EVENT ")) for line in captured.getvalue().splitlines()]
        self.assertEqual([e["type"] for e in relayed], ["progress", "kj_preview_override"])
        self.assertEqual({e["job_id"] for e in relayed}, {"fixture"})
        self.assertIn("127.0.0.1:8188/ws?clientId=fixture", websocket.create_connection.call_args.args[0])
        connection.close.assert_called_once()

    def test_model_cache_download_and_copy_do_not_add_content_scans(self):
        tree = ast.parse((RESOURCES / "worker_bootstrap.py").read_text())
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {"check_model_size", "prepare_model"}]
        code = compile(ast.Module(body=functions, type_ignores=[]), "model-preparation", "exec")
        for mode in ("cached", "download", "copy", "incomplete"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                source = root / "download.safetensors"
                source.write_bytes(b"fixture")
                target = root / "models/model.safetensors"
                target.parent.mkdir()
                if mode in {"cached", "incomplete"}:
                    target.write_bytes(b"short" if mode == "incomplete" else b"fixture")
                download = Mock(return_value=str(source))
                namespace = {"Path": Path, "os": os, "shutil": shutil, "LOCAL_MODELS": target.parent,
                             "RUN_ROOT": root, "hf_hub_download": download}
                exec(code, namespace)
                model = {"repo": "example/model", "revision": "pinned", "filename": "model.safetensors",
                         "destination": "model.safetensors", "size": 7}
                link = Mock(side_effect=OSError("cross-device link")) if mode == "copy" else os.link
                # The transfer itself may copy bytes. An additional Path.open
                # scan (as performed by the removed digest code) is forbidden.
                with patch.object(Path, "open", side_effect=AssertionError("Unexpected model content scan")), \
                     patch("os.link", link):
                    if mode == "incomplete":
                        with self.assertRaisesRegex(RuntimeError, "size mismatch"):
                            namespace["prepare_model"](model)
                    else:
                        namespace["prepare_model"](model)
                self.assertEqual(download.call_count, int(mode in {"download", "copy"}))
                if mode != "incomplete":
                    self.assertEqual(target.read_bytes(), b"fixture")

    def test_worker_result_describes_video_without_reading_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            output = root / "output"
            output.mkdir()
            video = output / "clip.mp4"
            video.write_bytes(b"fixture")
            request, result = root / "request.json", root / "result.json"
            request.write_text(json.dumps({"prompt": {}}))
            namespace = {"OUTPUT_DIR": output}
            exec((RESOURCES / "worker_job.py").read_text(), namespace)
            history = {"fixture": {"status": {"status_str": "success"},
                                  "outputs": {"1": {"images": [
                                      {"filename": "clip.mp4", "subfolder": "", "type": "output"}]}}}}
            namespace["h3_job_json"] = Mock(side_effect=[{"prompt_id": "fixture"}, history])
            original_open = Path.open

            def metadata_only(path, *args, **kwargs):
                if path == video:
                    raise AssertionError("Unexpected video content scan")
                return original_open(path, *args, **kwargs)

            with patch.object(Path, "open", metadata_only), redirect_stdout(io.StringIO()):
                namespace["h3_worker_job"](str(request), str(result))
            receipt = json.loads(result.read_text())
            self.assertTrue(receipt["success"], receipt.get("error"))
            self.assertEqual(receipt["files"], [{"filename": "clip.mp4", "subfolder": "", "size": 7,
                                                "remote_path": str(video)}])
