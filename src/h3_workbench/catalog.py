"""Resolve only the pinned models and node packages used by a queued graph."""
import copy
import json

from .config import RESOURCES

MODEL_INPUTS = {
    "UNETLoader": ("unet_name", "diffusion_models"),
    "CLIPLoader": ("clip_name", "text_encoders"),
    "VAELoader": ("vae_name", "vae"),
    "LoraLoaderModelOnly": ("lora_name", "loras"),
    "ModelPreviewOverrideKJ": ("tiny_vae", "vae_approx"),
}
H3_NODES = {"MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo"}


def reference_catalog():
    return json.loads((RESOURCES / "reference.json").read_text())


def all_models():
    base = json.loads((RESOURCES / "manifest.json").read_text())
    return list({m["destination"]: m for m in base["models"] + reference_catalog()["models"]}.values())


def reachable_nodes(prompt, targets=None):
    reachable, pending = set(), list(prompt if targets is None else targets)
    while pending:
        ident = str(pending.pop())
        if ident in reachable or ident not in prompt:
            continue
        reachable.add(ident)
        for value in prompt[ident].get("inputs", {}).values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                pending.append(value[0])
    return reachable


def plan_environment(prompt, targets=None):
    reachable = reachable_nodes(prompt, targets)
    kinds = {prompt[n].get("class_type") for n in reachable}
    if not kinds.intersection(H3_NODES):
        raise ValueError("此工作台支持 H3 文生、图生和参考素材生成工作流。")
    base = json.loads((RESOURCES / "manifest.json").read_text())
    reference = reference_catalog()
    catalog = {m["destination"]: m for m in all_models()}
    wanted = set()
    for ident in reachable:
        node = prompt[ident]
        selection = MODEL_INPUTS.get(node.get("class_type"))
        if not selection:
            continue
        field, category = selection
        value = node.get("inputs", {}).get(field)
        if field == "tiny_vae" and value in (None, "none"):
            continue
        if not isinstance(value, str):
            raise ValueError("请选择明确的模型文件；不支持动态模型路径。")
        key = category + "/" + value
        if key not in catalog:
            raise ValueError("模型尚未配置下载来源：" + value)
        wanted.add(key)
    # Minimal API callers may supply no loaders. Native complete workflows use
    # their exact selected filenames, including shared VAE files only once.
    if not wanted:
        defaults = reference["models"] if "MiniMaxH3ReferenceToVideo" in kinds else base["models"]
        wanted = {m["destination"] for m in defaults}
    result = copy.deepcopy(base)
    result["models"] = [catalog[key] for key in sorted(wanted)]
    result["extensions"] = [item for item in reference["extensions"] if
        kinds.intersection(item.get("node_types", [])) or
        any(k and k.startswith(item.get("node_prefix", "\0")) for k in kinds)]
    return result


def environment_key(manifest):
    return (
        frozenset((m["destination"], m["revision"]) for m in manifest["models"]),
        frozenset((e["name"], e["revision"]) for e in manifest.get("extensions", [])),
    )
