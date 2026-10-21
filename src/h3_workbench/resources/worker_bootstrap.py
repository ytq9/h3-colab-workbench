"""Executed only inside a user-requested Colab GPU session; MANIFEST is injected."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

RUN_ROOT = Path("/content/h3-workbench")
COMFY_DIR = RUN_ROOT / "ComfyUI"
LOCAL_MODELS = RUN_ROOT / "models"
FILES_ROOT = Path("/content/h3-worker-files")
INPUT_DIR, OUTPUT_DIR = FILES_ROOT / "input", FILES_ROOT / "output"
USER_DIR, LOG_DIR = FILES_ROOT / "user", FILES_ROOT / "logs"
for directory in (RUN_ROOT, LOCAL_MODELS, INPUT_DIR, OUTPUT_DIR, USER_DIR, LOG_DIR):
    directory.mkdir(parents=True, exist_ok=True)

H3_WORKER_BUSY = True
H3_WORKER_STARTED = H3_WORKER_TOUCHED = time.monotonic()
H3_WORKER_LEASE_UNTIL = 0


def should_release(now, started, busy, touched, lease_until):
    return now - started > 45 * 60 or (
        not busy and now - touched > 180 and now > lease_until
    )


def h3_worker_guard():
    while True:
        time.sleep(10)
        if should_release(time.monotonic(), H3_WORKER_STARTED, H3_WORKER_BUSY,
                          H3_WORKER_TOUCHED, H3_WORKER_LEASE_UNTIL):
            from google.colab import runtime
            runtime.unassign()
            return


threading.Thread(target=h3_worker_guard, daemon=True).start()


def run(arguments, **kwargs):
    subprocess.run([str(arg) for arg in arguments], check=True, **kwargs)


print("H3_STAGE 检查 GPU", flush=True)
gpu = subprocess.check_output(
    ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
    text=True, timeout=20,
).strip().splitlines()[0]
name, memory = gpu.rsplit(",", 1)
if "A100" not in name or float(memory) < 36 * 1024:
    raise RuntimeError("This release requires an A100 with at least 40 GB VRAM.")
needed = sum(m["size"] for m in MANIFEST["models"])
if shutil.disk_usage(RUN_ROOT).free < needed + 15 * 2**30:
    raise RuntimeError("Insufficient local Colab disk space for the pinned model set.")

print("H3_STAGE 安装生成环境", flush=True)
revision = MANIFEST["comfyui_revision"]
if not COMFY_DIR.exists():
    run(["git", "init", COMFY_DIR])
    run(["git", "-C", COMFY_DIR, "remote", "add", "origin", "https://github.com/Comfy-Org/ComfyUI.git"])
origin = subprocess.check_output(
    ["git", "-C", str(COMFY_DIR), "remote", "get-url", "origin"], text=True
).strip()
if origin != "https://github.com/Comfy-Org/ComfyUI.git":
    raise RuntimeError("Unexpected ComfyUI source.")
run(["git", "-C", COMFY_DIR, "fetch", "--depth", "1", "origin", revision])
run(["git", "-C", COMFY_DIR, "checkout", "--detach", revision])
extras = sorted({r for e in MANIFEST.get("extensions", []) for r in e.get("requirements", [])})
run([sys.executable, "-m", "pip", "install", "-q", "-r", COMFY_DIR / "requirements.txt",
     "huggingface_hub", "websocket-client==1.9.2", *extras])
CUSTOM_NODES = install_extensions(COMFY_DIR, MANIFEST.get("extensions", []))
run([sys.executable, "-c", "import torch; assert torch.cuda.is_available()"])
os.environ.update(
    HF_HUB_DISABLE_TELEMETRY="1", HF_HOME=str(RUN_ROOT / "hf-cache"),
    HF_XET_CHUNK_CACHE_SIZE_BYTES="0", HF_XET_HIGH_PERFORMANCE="1",
    HF_HUB_DISABLE_PROGRESS_BARS="1",
)

print("H3_STAGE 准备模型", flush=True)
from huggingface_hub import hf_hub_download


def check_model_size(path, model):
    if path.stat().st_size != model["size"]:
        raise RuntimeError("Model file size mismatch: " + model["destination"])


def prepare_model(model):
    target = LOCAL_MODELS / model["destination"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        check_model_size(target, model)
        return
    source = Path(hf_hub_download(
        repo_id=model["repo"], filename=model["filename"], revision=model["revision"],
        local_dir=str(RUN_ROOT / "downloads" / model["repo"].replace("/", "--") / model["revision"]),
        token=False,
    ))
    check_model_size(source, model)
    temporary = target.with_suffix(".part")
    temporary.unlink(missing_ok=True)
    try:
        os.link(source, temporary)
    except OSError:
        shutil.copyfile(source, temporary)
        check_model_size(temporary, model)
    temporary.replace(target)


with ThreadPoolExecutor(max_workers=2) as pool:
    pending = {pool.submit(prepare_model, m) for m in MANIFEST["models"]}
    while pending:
        done, pending = wait(pending, timeout=15, return_when=FIRST_COMPLETED)
        for future in done:
            future.result()
        print(f"Models ready: {len(MANIFEST['models']) - len(pending)}/{len(MANIFEST['models'])}", flush=True)

print("H3_STAGE 启动生成服务", flush=True)
with socket.socket() as probe:
    if probe.connect_ex(("127.0.0.1", 8188)) == 0:
        raise RuntimeError("Port 8188 is already occupied.")
arguments = [
    sys.executable, "-u", str(COMFY_DIR / "main.py"),
    "--listen", "127.0.0.1", "--port", "8188", "--disable-auto-launch",
    "--disable-partner-nodes", "--disable-all-custom-nodes", "--disable-assets",
    "--preview-method", "none", "--models-directory", str(LOCAL_MODELS),
    "--input-directory", str(INPUT_DIR), "--output-directory", str(OUTPUT_DIR),
    "--user-directory", str(USER_DIR), "--database-url", "sqlite:///" + str(RUN_ROOT / "comfyui.db"),
]
if CUSTOM_NODES:
    arguments += ["--whitelist-custom-nodes", *CUSTOM_NODES]
with (LOG_DIR / "comfyui.log").open("a") as handle:
    H3_PROCESS = subprocess.Popen(arguments, cwd=COMFY_DIR, stdout=handle, stderr=subprocess.STDOUT)
deadline = time.monotonic() + 180
while time.monotonic() < deadline:
    if H3_PROCESS.poll() is not None:
        raise RuntimeError("Remote ComfyUI exited; inspect the private runtime log.")
    try:
        with urllib.request.urlopen("http://127.0.0.1:8188/object_info", timeout=5) as response:
            info = json.load(response)
        if "MiniMaxH3ImageToVideo" not in info:
            raise RuntimeError("Required native H3 node is missing.")
        for extension in MANIFEST.get("extensions", []):
            if any(node not in info for node in extension.get("node_types", [])):
                raise RuntimeError("Required custom node failed to load: " + extension["name"])
        if any(e.get("node_prefix") == "VHS_" for e in MANIFEST.get("extensions", [])) and "VHS_VideoCombine" not in info:
            raise RuntimeError("Required video output nodes failed to load.")
        break
    except (urllib.error.URLError, TimeoutError):
        time.sleep(2)
else:
    raise RuntimeError("Remote ComfyUI startup timed out.")
H3_WORKER_TOUCHED = time.monotonic()
H3_WORKER_BUSY = False
print("H3_WORKER_READY", flush=True)
