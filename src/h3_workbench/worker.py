"""Render self-contained remote scripts from packaged, reviewable resources."""
import json

from .config import RESOURCES


def setup_script(manifest=None):
    manifest = manifest or json.loads((RESOURCES / "manifest.json").read_text())
    return ("MANIFEST = " + repr(manifest) + "\n"
            + (RESOURCES / "extension_install.py").read_text() + "\n"
            + (RESOURCES / "worker_bootstrap.py").read_text())


def job_script(request_file, result_file):
    return (
        (RESOURCES / "worker_job.py").read_text()
        + "\nh3_worker_job(" + repr(request_file) + ", " + repr(result_file) + ")\n"
    )


def lease_script(seconds):
    if not 30 <= seconds <= 600:
        raise ValueError("Transfer lease must be between 30 and 600 seconds.")
    return f"H3_WORKER_LEASE_UNTIL = time.monotonic() + {int(seconds)}\n"
