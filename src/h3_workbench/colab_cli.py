"""Pinned official Colab CLI with isolated credentials and minimal scopes."""
import json
import os
from pathlib import Path
import sys

from .config import load_settings


def accept_identity(private, email, expected, reconnecting):
    correct = email.lower() == expected
    if correct:
        (private / "identity.json").write_text(json.dumps({"email": email.lower()}))
    elif reconnecting:
        # A mistaken Google account selection must not replace the credentials
        # used to release an already-owned runtime under the expected account.
        (private / "token.json").unlink(missing_ok=True)
        previous = private / "previous-token.json"
        if previous.is_file():
            previous.replace(private / "token.json")
    return correct


def main():
    settings = load_settings()
    PRIVATE = settings.credential_dir
    EXPECTED_ACCOUNT = settings.account_email
    os.umask(0o077)
    PRIVATE.mkdir(parents=True, exist_ok=True)
    PRIVATE.chmod(0o700)
    from colab_cli import auth
    auth.TOKEN_CONFIG_PATH = str(PRIVATE / "token.json")
    # Files travel through the Colab contents API, then the Mac's existing
    # Drive sync. This integration does not need Cloud Platform or Drive API.
    auth.PUBLIC_SCOPES = [
        "openid", "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/colaboratory",
    ]
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in {"--h3-login", "--h3-check", "usage", "new", "exec", "upload", "download", "stop", "status", "sessions"}:
        raise ValueError("Unsupported workbench CLI operation.")
    token = Path(auth.TOKEN_CONFIG_PATH)
    if mode == "--h3-login":
        # Explicit reconnect from the local authorization page, isolated from
        # every other Google account/application on this computer.
        if token.exists():
            token.replace(PRIVATE / "previous-token.json")
    elif not token.is_file():
        print("H3_AUTH_REQUIRED")
        return 3
    if mode != "--h3-login":
        def interactive_auth_required(*args, **kwargs):
            print("H3_AUTH_REQUIRED")
            raise RuntimeError("Reconnect Google explicitly in the local workbench.")
        auth._run_remote_flow = interactive_auth_required
    if mode in ("--h3-login", "--h3-check"):
        session = auth.get_credentials(str(PRIVATE / "oauth-client.json"), provider=auth.AuthProvider.OAUTH2)
        response = session.get("https://openidconnect.googleapis.com/v1/userinfo", timeout=20)
        response.raise_for_status()
        email = response.json().get("email", "").lower()
        if token.exists():
            token.chmod(0o600)
        correct = accept_identity(PRIVATE, email, EXPECTED_ACCOUNT, mode == "--h3-login")
        print("H3_IDENTITY " + json.dumps({"email": email, "correct_account": correct}))
        return 0 if correct else 4
    from colab_cli.cli import app
    from colab_cli.common import state
    from colab_cli.history import HistoryLogger
    state._history = HistoryLogger(str(PRIVATE / "cli-history"))
    sys.argv[1:1] = ["--auth", "oauth2", "--client-oauth-config", str(PRIVATE / "oauth-client.json"), "--config", str(PRIVATE / "sessions.json")]
    app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
