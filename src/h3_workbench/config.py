"""Local configuration; credentials and user files never belong in the checkout."""
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sys

RESOURCES = Path(__file__).resolve().parent / "resources"


def app_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/H3 Workbench"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "h3-workbench"


def config_path():
    return Path(os.environ.get("H3_CONFIG", app_dir() / "config.json")).expanduser().resolve()


@dataclass(frozen=True)
class Settings:
    account_email: str
    project_dir: Path
    runtime_dir: Path
    credential_dir: Path
    cli_python: Path
    port: int = 8190
    browser: str = "Google Chrome"

    @property
    def editor_python(self):
        return self.runtime_dir / "editor-venv/bin/python"

    @property
    def comfy_dir(self):
        return self.runtime_dir / "ComfyUI"

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    @property
    def instance_id(self):
        # Stable identifier for ownership records; hashes only this short
        # configuration string, never model, video or archive contents.
        payload = f"{self.account_email}\n{self.project_dir}\n{self.runtime_dir}\n{self.port}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def validate(self):
        if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", self.account_email):
            raise ValueError("请配置有效的 Google 账号邮箱。")
        if type(self.port) is not int or not 1024 <= self.port <= 65535:
            raise ValueError("port 必须是 1024–65535 之间的整数。")
        if self.credential_dir.is_relative_to(self.project_dir):
            raise ValueError("credential_dir 必须位于素材/Drive 项目目录之外。")
        return self


def load_settings(path=None):
    path = Path(path) if path else config_path()
    if not path.is_file():
        raise RuntimeError("尚未配置。请先运行 h3-workbench init --email 你的邮箱。")
    data = json.loads(path.read_text())
    defaults = {
        "project_dir": Path.home() / "Movies/H3Workbench",
        "runtime_dir": app_dir() / "runtime",
        "credential_dir": app_dir() / "private",
        "cli_python": sys.executable,
    }
    for name, default in defaults.items():
        value = Path(data.get(name, default)).expanduser()
        # Resolving a virtualenv's Python symlink would silently select its
        # base interpreter and lose the installed Colab CLI dependencies.
        data[name] = value.absolute() if name == "cli_python" else value.resolve()
    data["account_email"] = data.get("account_email", "").strip().lower()
    return Settings(**data).validate()


def save_settings(settings, path=None):
    settings.validate()
    path = Path(path) if path else config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: str(v) if isinstance(v, Path) else v for k, v in asdict(settings).items()}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)
    return path


def subprocess_env(settings):
    env = dict(os.environ)
    env["H3_CONFIG"] = str(config_path())
    # Both Python environments can import this installed package, without
    # installing the GPU's dependencies or model weights on the Mac.
    imports = settings.runtime_dir / "pythonpath"
    imports.mkdir(parents=True, exist_ok=True)
    package = imports / "h3_workbench"
    target = Path(__file__).resolve().parent
    if package.is_symlink() and package.resolve() != target:
        package.unlink()
    if not package.exists():
        package.symlink_to(target, target_is_directory=True)
    elif not package.is_symlink():
        raise RuntimeError("运行环境 pythonpath 被非预期目录占用。")
    # Expose this package alone; do not mix the CLI virtualenv's other
    # site-packages into the independently pinned CPU editor environment.
    env["PYTHONPATH"] = str(imports)
    env.update(PYTHONUNBUFFERED="1", TOKENIZERS_PARALLELISM="false", NO_COLOR="1")
    return env
