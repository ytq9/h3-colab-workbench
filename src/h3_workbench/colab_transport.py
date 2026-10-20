"""Compatibility guards for google-colab-cli 0.7.4 / kernel client 0.9.0.

The pinned client ignores a false WebSocket readiness wait and its interactive
loop does not raise at the IOPub deadline. Keep these guards local to our CLI
process; never replay an execute_request after an uncertain connection failure.
"""
from functools import wraps
import importlib.metadata
import queue
import time


def bounded_execute(self, code, silent=False, store_history=True,
                    user_expressions=None, allow_stdin=None, stop_on_error=True,
                    timeout=None, output_hook=None, stdin_hook=None):
    """Receive matching IOPub + shell replies within one wall-clock budget."""
    deadline = time.monotonic() + (30 if timeout is None else timeout)

    def remaining():
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise TimeoutError("Colab execution response timed out; command was not replayed.")
        if not self.connection_ready.is_set():
            raise RuntimeError("Connection was lost; command was not replayed.")
        return budget

    allow_stdin = self.allow_stdin if allow_stdin is None else allow_stdin
    output_hook = output_hook or self._output_hook_default
    stdin_hook = stdin_hook or self._stdin_hook_default
    with self._interactive_lock:
        remaining()
        self.iopub_channel.get_msgs()
        msg_id = self.execute(code, silent=silent, store_history=store_history,
                              user_expressions=user_expressions, allow_stdin=allow_stdin,
                              stop_on_error=stop_on_error)
        while True:
            budget = remaining()
            if allow_stdin:
                try:
                    stdin_hook(self.stdin_channel.get_msg(timeout=0))
                except queue.Empty:
                    pass
            try:
                message = self.iopub_channel.get_msg(timeout=min(0.2, budget))
            except queue.Empty:
                continue
            if message["parent_header"].get("msg_id") != msg_id:
                continue
            output_hook(message)
            if (message["header"]["msg_type"] == "status"
                    and message["content"]["execution_state"] == "idle"):
                break
        # Poll the shell too: a disconnect after idle must not hang until the
        # 20-minute generation deadline, and stale replies are not our result.
        while True:
            try:
                reply = self.shell_channel.get_msg(timeout=min(0.2, remaining()))
            except queue.Empty:
                continue
            if reply["parent_header"].get("msg_id") == msg_id:
                return reply


def install_transport_guards():
    from colab_cli.runtime import ColabRuntime
    from jupyter_kernel_client.wsclient import KernelWebSocketClient

    if getattr(KernelWebSocketClient, "_h3_guarded", False):
        return
    if importlib.metadata.version("jupyter-kernel-client") != "0.9.0":
        raise RuntimeError("Unsupported kernel client; reinstall this release's pinned colab extra.")
    original_start = KernelWebSocketClient.start_channels
    original_execute = ColabRuntime.execute_code

    @wraps(original_start)
    def ready_start(self, *args, **kwargs):
        try:
            original_start(self, *args, **kwargs)
            # Upstream waits 10 seconds but ignores False. Give a delayed
            # handshake another 20 seconds, then close without sending code.
            if not self.connection_ready.is_set() and not self.connection_ready.wait(20):
                raise TimeoutError("Colab WebSocket handshake timed out before code submission.")
        except BaseException:
            self.stop_channels()
            raise

    @wraps(original_execute)
    def closing_execute(self, *args, **kwargs):
        try:
            return original_execute(self, *args, **kwargs)
        except BaseException:
            # The CLI's working-directory prelude is outside its try/finally.
            # Always close its background listener, without deleting the kernel.
            self.stop()
            raise

    KernelWebSocketClient.start_channels = ready_start
    KernelWebSocketClient.execute_interactive = bounded_execute
    ColabRuntime.execute_code = closing_execute
    KernelWebSocketClient._h3_guarded = True
