"""Shared pinned extension installer, runnable locally or inside Colab."""
from pathlib import Path
import json
import shutil
import tarfile
import tempfile
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def install_extensions(comfy_dir, extensions, source_root=None):
    wrappers = {
        "kj_preview": (
            "from .nodes.preview_override_node import ModelPreviewOverrideKJ, GetPreviewOverrideFramesKJ\n"
            "NODE_CLASS_MAPPINGS = {'ModelPreviewOverrideKJ': ModelPreviewOverrideKJ, "
            "'GetPreviewOverrideFramesKJ': GetPreviewOverrideFramesKJ}\nWEB_DIRECTORY = './web'\n"
        ),
        "goohai_ignore": (
            "from .nodes.忽略多组 import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS\n"
            "WEB_DIRECTORY = './web'\n"
        ),
    }
    installed = []
    parent = Path(comfy_dir) / "custom_nodes"
    parent.mkdir(parents=True, exist_ok=True)
    for item in extensions:
        target = parent / item["name"]
        receipt = target / "h3-source.json"
        if target.exists():
            if not receipt.is_file() or json.loads(receipt.read_text()) != item:
                raise RuntimeError("Existing custom-node folder does not match its pinned source: " + item["name"])
            installed.append(item["name"])
            continue
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            stage = Path(temporary) / "package"
            source = Path(source_root) / item["repo"].split("/")[1] if source_root else None
            if item.get("files"):
                stage.mkdir()
                def copy_file(relative):
                    output = stage / relative
                    output.parent.mkdir(parents=True, exist_ok=True)
                    if source and (source / relative).is_file():
                        shutil.copyfile(source / relative, output)
                    else:
                        url = "https://raw.githubusercontent.com/" + item["repo"] + "/" + item["revision"] + "/" + urllib.parse.quote(relative)
                        with urllib.request.urlopen(url, timeout=45) as response, output.open("wb") as handle:
                            shutil.copyfileobj(response, handle)
                with ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(copy_file, item["files"]))
            elif source and source.is_dir():
                shutil.copytree(source, stage, ignore=shutil.ignore_patterns("__pycache__", ".git"))
            else:
                package = Path(temporary) / "source.tar.gz"
                url = "https://codeload.github.com/" + item["repo"] + "/tar.gz/" + item["revision"]
                with urllib.request.urlopen(url, timeout=45) as response, package.open("wb") as handle:
                    shutil.copyfileobj(response, handle)
                unpack = Path(temporary) / "unpack"
                with tarfile.open(package) as archive:
                    archive.extractall(unpack, filter="data")
                roots = list(unpack.iterdir())
                if len(roots) != 1:
                    raise RuntimeError("Unexpected custom-node archive layout.")
                roots[0].rename(stage)
            if item.get("wrapper"):
                (stage / "__init__.py").write_text(wrappers[item["wrapper"]])
                (stage / "nodes/__init__.py").touch()
            (stage / "h3-source.json").write_text(json.dumps(item, ensure_ascii=False, indent=2))
            stage.rename(target)
        installed.append(item["name"])
    return installed
