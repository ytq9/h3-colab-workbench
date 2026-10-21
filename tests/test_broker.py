"""On-demand lifecycle/transfer checks against a fake CLI; never allocate a VM."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from h3_workbench.broker import ColabBroker
from h3_workbench.config import Settings
import sys
from h3_workbench.worker import setup_script, job_script
from h3_workbench.executor import executor_class

PROMPT = {"1": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {}}}


class FakeBroker(ColabBroker):
    def __init__(self, root):
        root = Path(root)
        settings = Settings("you@example.com", root / "project", root / "local", root / "private", Path(sys.executable))
        super().__init__(settings=settings)
        self.calls = []
        self.balance = "191.82"
        self.identity_ok = True
        self.fail_at = None
        self.incomplete_download = False
        self.release_fails = False
        self.active_assignments = 0

    def command(self, args, **kwargs):
        args = list(args)
        self.calls.append(args)
        if args[0] == self.fail_at:
            raise RuntimeError("synthetic CLI failure")
        if args[0] == "--h3-check":
            return "H3_IDENTITY " + json.dumps({"email": "you@example.com" if self.identity_ok else "other@example.com",
                                               "correct_account": self.identity_ok})
        if args[0] == "usage":
            return "Current balance: " + self.balance + " compute units\nUsage rate: 0.0/hr\nActive assignments: " + str(self.active_assignments)
        if args[0] == "new":
            self.active_assignments = 1
            return "Allocated fixture"
        if args[0] == "exec":
            return "H3_WORKER_READY"
        if args[0] == "download":
            target = Path(args[-1])
            target.parent.mkdir(parents=True, exist_ok=True)
            if args[-2].endswith("-result.json"):
                target.write_text(json.dumps({
                    "success": True, "messages": [], "meta": {},
                    "outputs": {"92": {"images": [{"filename": "clip.mp4", "subfolder": "video", "type": "output"}]}},
                    "files": [{"filename": "clip.mp4", "subfolder": "video", "size": 7,
                               "remote_path": "/content/h3-worker-files/output/video/clip.mp4"}]}))
            else:
                target.write_bytes(b"short" if self.incomplete_download else b"fixture")
            return "Downloaded"
        if args[0] == "stop":
            if self.release_fails:
                raise OSError("synthetic stop failure")
            self.active_assignments = 0
            return "Session terminated."
        return ""


class LocalBrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.broker = FakeBroker(self.temp.name)

    def run_job(self, more=False, prompt=None, extra=None):
        return self.broker.execute(prompt or PROMPT, str(uuid.uuid4()), extra or {}, lambda: more)

    def test_opening_editor_does_not_contact_colab(self):
        self.assertEqual(self.broker.calls, [])
        self.assertIsNone(self.broker.session)
        self.assertFalse(self.broker.status()["gpu_session"])

    def test_success_without_digest_checks_size_releases_and_rewrites_preview(self):
        result = self.run_job()
        commands = [call[0] for call in self.broker.calls]
        self.assertEqual(commands[:3], ["--h3-check", "usage", "new"])
        self.assertEqual(commands[-1], "stop")
        self.assertEqual(commands.count("new"), 1)
        video = result["outputs"]["92"]["images"][0]
        self.assertEqual((self.broker.project / "output" / video["subfolder"] / video["filename"]).read_bytes(), b"fixture")
        self.assertTrue(self.broker.release_confirmed)
        self.assertIsNone(self.broker.session)

    def test_queued_jobs_reuse_one_gpu_then_stop_when_drained(self):
        self.run_job(more=True)
        self.assertIsNotNone(self.broker.session)
        self.run_job()
        commands = [call[0] for call in self.broker.calls]
        self.assertEqual(commands.count("new"), 1)
        self.assertEqual(commands.count("stop"), 1)
        self.assertEqual(len(list((self.broker.project / "output/video").glob("*/video/clip.mp4"))), 2)

    def test_wrong_account_empty_or_unknown_balance_never_allocates(self):
        for bad in ("account", "zero", "unreadable"):
            with self.subTest(bad=bad):
                broker = FakeBroker(Path(self.temp.name) / bad)
                broker.identity_ok = bad != "account"
                broker.balance = "0" if bad == "zero" else "unavailable" if bad == "unreadable" else "100"
                with self.assertRaises(RuntimeError):
                    broker.execute(PROMPT, str(uuid.uuid4()), {}, lambda: False)
                self.assertNotIn("new", [c[0] for c in broker.calls])

    def test_setup_or_transfer_failure_releases_even_with_pending_jobs(self):
        for failure in ("exec", "upload", "download"):
            broker = FakeBroker(Path(self.temp.name) / failure)
            broker.fail_at = failure
            with self.assertRaises(RuntimeError):
                broker.execute(PROMPT, str(uuid.uuid4()), {}, lambda: True)
            self.assertEqual(broker.calls[-1][0], "stop")
            self.assertIsNone(broker.session)

    def test_incomplete_download_is_not_reported_as_complete(self):
        self.broker.incomplete_download = True
        with self.assertRaisesRegex(RuntimeError, "大小不符"):
            self.run_job()
        self.assertEqual(self.broker.calls[-1][0], "stop")
        self.assertEqual(list((self.broker.project / "output").rglob("*.mp4")), [])

    def test_video_download_does_not_scan_the_received_file(self):
        original_open = Path.open
        def metadata_only(path, mode="r", *args, **kwargs):
            if "r" in mode and "b" in mode and path.suffix in {".mp4", ".part"}:
                raise AssertionError("Unexpected video content scan")
            return original_open(path, mode, *args, **kwargs)
        with patch.object(Path, "open", metadata_only):
            result = self.run_job()
        self.assertTrue(result["success"])
        self.assertTrue(self.broker.release_confirmed)

    def test_uncertain_release_blocks_reuse_and_reports_attention(self):
        self.broker.release_fails = True
        self.run_job()
        self.assertFalse(self.broker.release_confirmed)
        self.assertIn("释放未确认", self.broker.stage)
        count = sum(c[0] == "new" for c in self.broker.calls)
        with self.assertRaises(RuntimeError):
            self.run_job()
        self.assertEqual(count, sum(c[0] == "new" for c in self.broker.calls))

    def test_disappeared_runtime_is_reconciled_after_stop_fails(self):
        self.run_job(more=True)
        self.broker.release_fails = True
        self.broker.active_assignments = 0
        allocations = sum(c[0] == "new" for c in self.broker.calls)
        self.assertTrue(self.broker.stop_gpu())
        self.assertTrue(self.broker.release_confirmed)
        self.assertIsNone(self.broker.session)
        self.assertEqual(self.broker.stage, "本机 CPU 编辑 · GPU 已释放")
        self.assertEqual([c[0] for c in self.broker.calls[-2:]], ["stop", "usage"])
        self.assertEqual(allocations, sum(c[0] == "new" for c in self.broker.calls))

    def test_failed_release_with_unknown_cloud_status_remains_unconfirmed(self):
        for failure in ("unreadable", "request_failed"):
            with self.subTest(failure=failure):
                broker = FakeBroker(Path(self.temp.name) / failure)
                broker.execute(PROMPT, str(uuid.uuid4()), {}, lambda: True)
                broker.release_fails = True
                broker.active_assignments = "unavailable"
                if failure == "request_failed":
                    broker.fail_at = "usage"
                self.assertFalse(broker.stop_gpu())
                self.assertFalse(broker.release_confirmed)
                self.assertIsNotNone(broker.session)

    def test_only_file_transfer_is_retried_without_reallocating(self):
        original = self.broker.command
        attempts = []
        def flaky(args, **kwargs):
            if args[0] == "upload":
                attempts.append(list(args))
                if len(attempts) == 1:
                    raise RuntimeError("transient upload failure")
            return original(args, **kwargs)
        self.broker.command = flaky
        with patch.object(self.broker.cancelled, "wait", return_value=False):
            self.run_job()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(sum(c[0] == "new" for c in self.broker.calls), 1)
        generation_calls = [c for c in self.broker.calls if c[0] == "exec" and "remote-job.py" in " ".join(c)]
        self.assertEqual(len(generation_calls), 1)

    def test_restart_retains_owned_session_until_release_is_confirmed(self):
        self.run_job(more=True)
        session = self.broker.session
        restarted = FakeBroker(self.temp.name)
        self.assertEqual(restarted.session, session)
        self.assertFalse(restarted.release_confirmed)
        self.assertFalse(restarted.worker_ready)
        self.assertEqual(restarted.calls, [])
        with self.assertRaises(RuntimeError):
            restarted.ensure_gpu()
        self.assertTrue(restarted.stop_gpu())
        final = FakeBroker(self.temp.name)
        self.assertIsNone(final.session)
        self.assertTrue(final.release_confirmed)

    def test_primary_error_logs_survive_cleanup_failure(self):
        from h3_workbench.broker import ColabBroker
        self.broker.cli = [sys.executable, "-c", "import sys; print('fixture failure'); sys.exit(1)"]
        for operation in ("upload", "stop"):
            with self.assertRaises(RuntimeError):
                ColabBroker.command(self.broker, [operation], timeout=5)
        logs = list((self.broker.private / "logs").glob("*.log"))
        self.assertEqual(len(logs), 2)
        self.assertEqual({p.name.split("-")[0] for p in logs}, {"upload", "stop"})
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in logs))

    def test_post_upload_failure_has_submission_stage_and_is_not_replayed(self):
        original = self.broker.command
        submitted = []
        def fail_submission(args, **kwargs):
            if args[0] == "exec" and "remote-job.py" in " ".join(args):
                submitted.append(self.broker.stage)
                raise RuntimeError("Connection was lost.")
            return original(args, **kwargs)
        self.broker.command = fail_submission
        with self.assertRaisesRegex(RuntimeError, "Connection was lost"):
            self.run_job()
        self.assertEqual(submitted, ["素材已上传，正在连接生成执行器"])
        self.assertTrue(self.broker.release_confirmed)

    def test_assets_are_snapshotted_and_secrets_are_not_in_job(self):
        inputs = self.broker.project / "input";inputs.mkdir(parents=True)
        (inputs / "source.png").write_bytes(b"image")
        prompt = dict(PROMPT, image={"class_type": "LoadImage", "inputs": {"image": "source.png"}})
        folder, request, assets = self.broker.prepare_job(
            prompt, str(uuid.uuid4()), {"api_key": "MUST_NOT_TRAVEL", "extra_pnginfo": {"workflow": {"nodes": []}}})
        self.assertEqual(assets[0][0].read_bytes(), b"image")
        self.assertNotIn("MUST_NOT_TRAVEL", request.read_text())
        self.assertEqual(prompt["image"]["inputs"]["image"], "source.png")

    def test_input_traversal_and_foreign_output_paths_are_rejected(self):
        for name in ("../../secret", "/etc/passwd", "x\\y"):
            prompt = dict(PROMPT, image={"class_type": "LoadImage", "inputs": {"image": name}})
            with self.assertRaises(ValueError):
                self.run_job(prompt=prompt)
        self.assertEqual(self.broker.calls, [])
        self.broker.session = "fixture"
        bad = {"files": [{"filename": "secret", "subfolder": "", "remote_path": "/etc/secret", "size": 1}]}
        with self.assertRaises(ValueError):
            self.broker.retrieve_result(Path(self.temp.name) / "job", bad)
        self.assertEqual(self.broker.calls, [])

    def test_executor_preserves_native_queue_history_contract(self):
        server = SimpleNamespace(send_sync=Mock(), prompt_queue=SimpleNamespace(get_tasks_remaining=lambda: 1))
        executor = executor_class(self.broker)(server)
        ident = str(uuid.uuid4())
        executor.execute(PROMPT, ident, {"client_id": "browser"}, ["1"])
        self.assertTrue(executor.success)
        self.assertIn("92", executor.history_result["outputs"])
        self.assertIn("executed", [c.args[0] for c in server.send_sync.call_args_list])
        executor.execute({}, str(uuid.uuid4()), {}, [])
        self.assertFalse(executor.success)
        self.assertEqual(executor.status_messages[-1][0], "execution_error")

    def test_partial_execution_does_not_start_gpu_for_unrelated_output(self):
        prompt = dict(PROMPT, other={"class_type": "SaveImage", "inputs": {}})
        with self.assertRaises(ValueError):
            self.broker.execute(prompt, str(uuid.uuid4()), {}, lambda: False, partial_targets=["other"])
        self.assertEqual(self.broker.calls, [])

    def test_remote_scripts_compile_without_drive_mount_or_public_entry(self):
        setup = setup_script()
        compile(setup, "setup", "exec")
        compile(job_script("/content/job.json", "/content/result.json"), "job", "exec")
        self.assertNotIn("drive.mount(", setup)
        self.assertNotIn("cloudflared", setup)
        self.assertIn("--listen", setup)
        self.assertIn('"127.0.0.1"', setup)
        self.assertIn("ThreadPoolExecutor", setup)


if __name__ == "__main__":
    unittest.main()
