import logging
import unittest

from h3_workbench.diagnostics import RedactedFormatter, TransportEvents, without_preview_events


class DiagnosticTests(unittest.TestCase):
    def test_streamed_preview_chunks_are_not_persisted_in_execution_history(self):
        chunks = ["H3_STAGE Generating\nH3_EV", 'ENT {"image":"private-', 'frame"}\nH3_JOB_FINISHED\n']
        original = {"code": "job()", "outputs": [
            {"output_type": "stream", "name": "stdout", "text": chunk} for chunk in chunks
        ] + [{"output_type": "error", "evalue": "failed"}]}
        result = without_preview_events(original)
        self.assertEqual(result["outputs"][0]["text"], "H3_STAGE Generating\nH3_JOB_FINISHED\n")
        self.assertEqual(result["outputs"][1], original["outputs"][3])
        self.assertEqual(original["outputs"][1]["text"], chunks[1])
        self.assertNotIn("private-frame", str(result))

    def test_wire_bodies_are_dropped_and_warning_credentials_redacted(self):
        gate = TransportEvents()
        for logger, message in (
            ("colab_cli.client", "Request Headers: fixture-secret"),
            ("jupyter_kernel_client", "WSSession.send\nprivate code and prompt"),
            ("jupyter_kernel_client", "Ignoring message not from request: private result"),
        ):
            self.assertFalse(gate.filter(logging.LogRecord(logger, logging.DEBUG, "", 0, message, (), None)))
        event = logging.LogRecord("jupyter_kernel_client", logging.DEBUG, "", 0,
                                  "Websocket connection is ready.", (), None)
        self.assertTrue(gate.filter(event))
        warning = logging.LogRecord("websocket", logging.WARNING, "", 0,
            "wss://fixture.invalid/path?token=fixture-secret "
            "{'X-Colab-Runtime-Proxy-Token': 'second-secret', 'authorization': 'Bearer third-secret'}",
            (), None)
        rendered = RedactedFormatter().format(warning)
        for value in ("fixture-secret", "second-secret", "third-secret"):
            self.assertNotIn(value, rendered)
