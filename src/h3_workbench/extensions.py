"""Install the original node implementations needed by reference workflows."""
import subprocess

from .catalog import reference_catalog
from .resources.extension_install import install_extensions


def install_reference_extensions(settings, source_root=None):
    extensions = reference_catalog()["extensions"]
    requirements = sorted({r for e in extensions for r in e.get("requirements", [])})
    subprocess.run([str(settings.editor_python), "-m", "pip", "install", *requirements], check=True)
    names = install_extensions(settings.comfy_dir, extensions, source_root)
    subprocess.run([str(settings.editor_python), "-m", "pip", "check"], check=True)
    return names


def installed_extensions(settings):
    return [item["name"] for item in reference_catalog()["extensions"]
            if (settings.comfy_dir / "custom_nodes" / item["name"] / "h3-source.json").is_file()]
