"""Replay delayed Colab WebSocket readiness without a VM or network."""
import json
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

try:
    from colab_cli.runtime import ColabRuntime
    from jupyter_kernel_client import KernelClient
    from jupyter_kernel_client.wsclient import KernelWebSocketClient
except ImportError:
    ColabRuntime = None


@unittest.skipIf(ColabRuntime is None, "Install the colab extra for transport regression tests")
class ColabTransportTests(unittest.TestCase):
    def setUp(self):
        from h3_workbench.colab_transport import install_transport_guards
        install_transport_guards()

    def test_slow_handshake_finishes_prelude_before_submitting_job(self):
        ws = KernelWebSocketClient("ws://fixture.invalid/api/kernels/fixture/channels")
        sent = []

        class DelayedReady(threading.Event):
            def __init__(self):
                super().__init__()
                self.waits = 0

            def wait(self, timeout=None):
                self.waits += 1
                if self.waits == 1:
                    # The initial 10-second upstream wait elapsed, as in the trace.
                    return False
                self.set()
                return True

        ws.connection_ready = DelayedReady()

        class Socket:
            def __init__(self, *args, **kwargs):
                self.on_message = kwargs["on_message"]

            def send_text(self, wire):
                request = json.loads(wire)
                sent.append(request["content"]["code"])
                if not ws.connection_ready.is_set():
                    return
                for channel, kind, content in (
                    ("iopub", "status", {"execution_state": "busy"}),
                    ("iopub", "status", {"execution_state": "idle"}),
                    ("shell", "execute_reply", {"status": "ok", "execution_count": 1}),
                ):
                    msg = ws.session.msg(kind, content=content, parent=request)
                    msg["channel"] = channel
                    self.on_message(self, json.dumps(msg, default=str))

            def close(self):
                ws.connection_ready.clear()

        class FixtureKernel(KernelClient):
            def __init__(self, **kwargs):
                super(KernelClient, self).__init__()
                self._manager = SimpleNamespace(client=ws, has_kernel=True, kernel={"id": "fixture"})
                self._own_kernel = False

        runtime = ColabRuntime("https://fixture.invalid", "fixture", kernel_id="fixture")
        with patch("jupyter_kernel_client.KernelClient", FixtureKernel), \
             patch("jupyter_kernel_client.wsclient.websocket.WebSocketApp", Socket), \
             patch("jupyter_kernel_client.wsclient.Thread", Mock()):
            try:
                runtime.execute_code("# CLI working-directory prelude", timeout=1)
                runtime.execute_code("# H3 generation job", timeout=1)
                self.assertEqual(sent, ["# CLI working-directory prelude", "# H3 generation job"])
            finally:
                runtime.stop()

    def test_never_ready_socket_closes_without_sending_code(self):
        ws = KernelWebSocketClient("ws://fixture.invalid/api/kernels/fixture/channels")
        ws.connection_ready = Mock(wait=Mock(return_value=False), is_set=Mock(return_value=False))
        with patch("jupyter_kernel_client.wsclient.websocket.WebSocketApp") as socket, \
             patch("jupyter_kernel_client.wsclient.Thread", Mock()):
            try:
                with self.assertRaises(TimeoutError):
                    ws.start_channels()
                socket.return_value.send_text.assert_not_called()
                socket.return_value.close.assert_called_once()
            finally:
                ws.stop_channels()

    def test_silent_execution_honors_deadline_without_resubmission(self):
        ws = KernelWebSocketClient("ws://fixture.invalid/api/kernels/fixture/channels")
        ws.connection_ready.set()
        ws.kernel_socket = Mock()
        # A deterministic clock, plus a tripwire instead of an infinite old loop.
        clock = iter([n / 10 for n in range(30)])
        ws._message_received = Mock()
        ws._message_received.wait.side_effect = [None] * 6 + [RuntimeError("old loop exceeded deadline")]
        with patch("time.monotonic", side_effect=lambda: next(clock)):
            try:
                with self.assertRaises(TimeoutError):
                    ws.execute_interactive("# quiet prelude", timeout=0.2, allow_stdin=False)
                ws.kernel_socket.send_text.assert_called_once()
            finally:
                ws.stop_channels()

    def test_failed_prelude_closes_client_even_before_cli_finally(self):
        runtime = ColabRuntime("https://fixture.invalid", "fixture", kernel_id="fixture")
        runtime._kernel_client = Mock()
        runtime._kernel_client.execute.side_effect = RuntimeError("Connection was lost.")
        with self.assertRaisesRegex(RuntimeError, "Connection was lost"):
            runtime.execute_code("# CLI prelude", timeout=1)
        runtime._kernel_client._manager.client.stop_channels.assert_called_once()


if __name__ == "__main__":
    unittest.main()
