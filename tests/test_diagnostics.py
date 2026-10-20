import logging
import unittest

from h3_workbench.diagnostics import RedactedFormatter, TransportEvents


class DiagnosticTests(unittest.TestCase):
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
