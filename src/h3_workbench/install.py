"""Install the CPU editor only. This module never contacts Colab."""
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import venv

from .config import RESOURCES
from .presets import make_presets


def install_editor(settings, wheelhouse=None, archive=None):
    if sys.platform != "darwin" or platform.machine() != "arm64" or sys.version_info[:2] != (3, 12):
        raise RuntimeError("The verified editor installer requires Apple Silicon macOS and Python 3.12.")
    if int(platform.mac_ver()[0].split(".")[0] or "0") < 14:
        raise RuntimeError("The pinned CPU wheels require macOS 14 or newer.")
    manifest = json.loads((RESOURCES / "manifest.json").read_text())
    runtime = settings.runtime_dir
    runtime.mkdir(parents=True, exist_ok=True)
    receipt = runtime / "source-receipt.json"
    if settings.comfy_dir.exists():
        if not receipt.is_file() or json.loads(receipt.read_text()).get("revision") != manifest["comfyui_revision"]:
            raise RuntimeError("Existing ComfyUI has no matching pinned-source receipt. Use an empty runtime_dir.")
    else:
        with tempfile.TemporaryDirectory(dir=runtime) as temporary:
            temp = Path(temporary)
            package = temp / "comfyui.tar.gz"
            if archive:
                shutil.copyfile(archive, package)
            else:
                with urllib.request.urlopen(manifest["comfyui_archive"]["url"], timeout=60) as response, package.open("wb") as out:
                    shutil.copyfileobj(response, out)
            unpack = temp / "source"
            unpack.mkdir()
            with tarfile.open(package) as tar:
                tar.extractall(unpack, filter="data")
            roots = list(unpack.iterdir())
            if len(roots) != 1 or not (roots[0] / "main.py").is_file():
                raise RuntimeError("Unexpected ComfyUI source archive layout.")
            roots[0].rename(settings.comfy_dir)
            receipt.write_text(json.dumps({
                "revision": manifest["comfyui_revision"],
                "url": manifest["comfyui_archive"]["url"],
            }, indent=2) + "\n")
    if not settings.editor_python.is_file():
        venv.EnvBuilder(with_pip=True).create(runtime / "editor-venv")
    arguments = [str(settings.editor_python), "-m", "pip", "install", "-r", str(RESOURCES / "editor-macos-arm64.txt")]
    if wheelhouse:
        arguments += ["--no-index", "--find-links", str(Path(wheelhouse).resolve())]
    subprocess.run(arguments, check=True)
    subprocess.run([str(settings.editor_python), "-m", "pip", "check"], check=True)
    for name in ("input", "output", "user"):
        (settings.project_dir / name).mkdir(parents=True, exist_ok=True)
    workflows = settings.project_dir / "user/default/workflows/H3"
    workflows.mkdir(parents=True, exist_ok=True)
    for name, graph in make_presets(RESOURCES / "workflows").items():
        path = workflows / name
        if not path.exists():
            path.write_text(json.dumps(graph, ensure_ascii=False, indent=2) + "\n")
    print("CPU editor installed. No GPU was allocated and no model weights were downloaded.")
