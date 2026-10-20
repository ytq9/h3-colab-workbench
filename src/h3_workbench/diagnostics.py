"""Private, bounded transport events without request headers or payloads."""
import logging
from logging.handlers import RotatingFileHandler
import re


def redact_diagnostic(text):
    text = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~-]+", r"\1<REDACTED>", text)
    text = re.sub(r"eyJ[A-Za-z0-9_.-]{20,}", "<REDACTED>", text)
    text = re.sub(
        r"""(?i)((?:access_token|refresh_token|id_token|client_secret|authorization|cookie|token|password|X-Colab-Runtime-Proxy-Token|X-Goog-Colab-Token)["']?\s*[:=]\s*["']?)[^"'\s,}]+""",
        r"\1<REDACTED>", text,
    )
    return re.sub(r"((?:https?|wss?)://[^\s?]+)\?[^\s]+", r"\1?<REDACTED>", text)


class TransportEvents(logging.Filter):
    def filter(self, record):
        # In particular, never keep WSSession.send's code dump or HTTP debug
        # headers. Event types + timing are sufficient to diagnose readiness.
        if record.levelno >= logging.WARNING:
            return True
        return record.name in {"jupyter_kernel_client", "websocket"} and str(record.msg).startswith((
            "Sending message on channel:", "Received message on channel:",
            "Starting the kernel client", "Stopping the kernel client",
            "Websocket connection is ready", "Websocket connection is closed",
            "Websocket connected",
        ))


class RedactedFormatter(logging.Formatter):
    def format(self, record):
        return redact_diagnostic(super().format(record))


def configure_cli_logging(private):
    """Replace the CLI's unconditional global DEBUG file in our subprocess."""
    directory = private / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    private.chmod(0o700)
    directory.chmod(0o700)
    path = directory / "transport.log"
    handler = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=2, encoding="utf-8")
    path.chmod(0o600)
    handler.addFilter(TransportEvents())
    handler.setFormatter(RedactedFormatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    for previous in root.handlers[:]:
        root.removeHandler(previous)
        previous.close()
    root.setLevel(logging.WARNING)
    root.addHandler(handler)
    logging.getLogger("jupyter_kernel_client").setLevel(logging.DEBUG)
    logging.getLogger("websocket").setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
