#!/usr/bin/env python3
"""Read-only/native validation against a running CPU editor; never queue a job."""
import json
from pathlib import Path
import urllib.error
import urllib.request

from h3_workbench.config import load_settings
from h3_workbench.net import local_json


def main():
    settings = load_settings()
    state = local_json(settings.url + "/h3/status")
    assert state["instance_id"] == settings.instance_id, "Unexpected workbench instance"
    stats = local_json(settings.url + "/system_stats")
    assert stats["devices"][0]["type"] == "cpu", "Editor is not running on CPU"
    nodes = local_json(settings.url + "/object_info")
    assert "MiniMaxH3ImageToVideo" in nodes
    fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/minimum_t2v_api.json"
    result = local_json(settings.url + "/h3/validate", {"prompt": json.loads(fixture.read_text())})
    assert result["valid"] and not result["node_errors"], result
    extensions = local_json(settings.url + "/extensions")
    assert any("h3-colab.js" in entry for entry in extensions)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    checks = [
        urllib.request.Request(settings.url + "/h3/status", headers={"Host": "untrusted.example"}),
        urllib.request.Request(
            settings.url + "/h3/validate", data=b'{"prompt":{}}',
            headers={"Origin": "http://untrusted.example", "Content-Type": "application/json"},
        ),
    ]
    for request in checks:
        try:
            opener.open(request, timeout=5)
        except urllib.error.HTTPError as error:
            assert error.code == 403
        else:
            raise AssertionError("Local request boundary was not enforced")
    print("PASS: CPU editor, native H3 workflow validation, browser extension, Host/Origin checks. No job queued.")


if __name__ == "__main__":
    main()
