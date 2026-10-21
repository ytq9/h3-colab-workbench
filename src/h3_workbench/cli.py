"""Human-facing project commands. Starting the editor never allocates a GPU."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

from . import __version__
from .config import Settings, app_dir, config_path, load_settings, save_settings, subprocess_env
from .net import local_json


def start(settings, no_browser=False):
    try:
        state = local_json(settings.url + "/h3/status", timeout=2)
    except Exception:
        state = None
    if state is None:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", settings.port)) == 0:
                raise RuntimeError(f"Port {settings.port} is occupied by a different service.")
        if not settings.editor_python.is_file() or not (settings.comfy_dir / "main.py").is_file():
            raise RuntimeError("请先运行 h3-workbench install。")
        settings.runtime_dir.mkdir(parents=True, exist_ok=True)
        log_path = settings.runtime_dir / "editor.log"
        with log_path.open("a") as log:
            log_path.chmod(0o600)
            process = subprocess.Popen(
                [str(settings.editor_python), "-u", "-m", "h3_workbench.editor"],
                cwd=str(settings.comfy_dir), stdout=log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True, env=subprocess_env(settings),
            )
        (settings.runtime_dir / "editor.pid").write_text(str(process.pid))
        for _ in range(180):
            if process.poll() is not None:
                raise RuntimeError(f"本机启动失败，请查看 {log_path}")
            try:
                state = local_json(settings.url + "/h3/status", timeout=1)
                break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError(f"本机启动尚未完成，请查看 {log_path}")
    if state.get("instance_id") != settings.instance_id:
        raise RuntimeError("此端口上已有另一份配置或旧版工作台。请先结束它，或配置不同端口。")
    print(settings.url)
    print("本机 CPU 编辑；点击运行才申请 Colab GPU。")
    if not no_browser:
        if sys.platform == "darwin":
            subprocess.run(["open", "-a", settings.browser, settings.url], check=True)
        else:
            print("请在浏览器中打开以上地址。")


def stop(settings):
    require_instance(settings)
    queue = local_json(settings.url + "/queue")
    if queue["queue_running"] or queue["queue_pending"]:
        raise RuntimeError("队列仍有任务；请先等待完成，或在 ComfyUI 中中止。")
    result = local_json(settings.url + "/h3/release", {}, timeout=180)
    if not result.get("released"):
        raise RuntimeError("GPU 释放未确认；保留本机后台，便于重试释放。")
    pid_file = settings.runtime_dir / "editor.pid"
    pid = int(pid_file.read_text())
    command = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="], text=True)
    if "h3_workbench.editor" not in command:
        raise RuntimeError("PID 与当前工作台不匹配；未停止其他进程。")
    os.kill(pid, signal.SIGTERM)
    print("已请求停止本机后台；视频和工作流保留。")


def require_instance(settings):
    state = local_json(settings.url + "/h3/status")
    if state.get("instance_id") != settings.instance_id:
        raise RuntimeError("该端口上的工作台与当前配置不匹配；未修改其他工作台。")
    return state


def doctor(settings):
    try:
        cli_version = importlib.metadata.version("google-colab-cli")
    except importlib.metadata.PackageNotFoundError:
        cli_version = None
    report = {
        "workbench_version": __version__, "python": sys.version.split()[0],
        "config": str(config_path()), "project_dir": str(settings.project_dir),
        "editor_installed": settings.editor_python.is_file() and (settings.comfy_dir / "main.py").is_file(),
        "colab_cli_version": cli_version,
        "saved_authorization": (settings.credential_dir / "token.json").is_file(),
    }
    try:
        state = local_json(settings.url + "/h3/status", timeout=2)
        report["editor"] = {k: state.get(k) for k in ("stage", "gpu_session", "release_confirmed", "instance_id")}
    except Exception:
        report["editor"] = "not running"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["editor_installed"] and cli_version == "0.7.4" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="h3-workbench")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create local configuration; no GPU or login")
    init.add_argument("--email", required=True)
    init.add_argument("--project-dir", default=str(Path.home() / "Movies/H3Workbench"))
    init.add_argument("--runtime-dir", default=str(app_dir() / "runtime"))
    init.add_argument("--port", type=int, default=8190)
    init.add_argument("--force", action="store_true")
    install = sub.add_parser("install", help="Install the pinned CPU editor")
    install.add_argument("--wheelhouse", type=Path)
    install.add_argument("--archive", type=Path)
    sub.add_parser("install-reference", help="Install pinned reference workflow nodes on the Mac; no GPU")
    launch = sub.add_parser("start", help="Start or reuse the local CPU editor")
    launch.add_argument("--no-browser", action="store_true")
    sub.add_parser("stop", help="Release GPU and stop an idle local editor")
    sub.add_parser("status", help="Show local workbench state")
    sub.add_parser("doctor", help="Check local installation without contacting Colab")
    sub.add_parser("usage", help="Read current Colab credits without allocating")
    sub.add_parser("release", help="Retry release of this idle workbench's GPU")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            if config_path().exists() and not args.force:
                raise RuntimeError("配置已存在。保留现有配置，或显式使用 --force 更新。")
            settings = Settings(
                args.email.strip().lower(), Path(args.project_dir).expanduser().resolve(),
                Path(args.runtime_dir).expanduser().resolve(), (app_dir() / "private").resolve(),
                Path(sys.executable).absolute(), args.port,
            )
            print(save_settings(settings))
            return 0
        settings = load_settings()
        if args.command == "install":
            from .install import install_editor
            install_editor(settings, args.wheelhouse, args.archive)
        elif args.command == "start":
            start(settings, args.no_browser)
        elif args.command == "install-reference":
            from .extensions import install_reference_extensions
            print("Installed: " + ", ".join(install_reference_extensions(settings)))
            print("请在当前队列为空、GPU 释放后重新启动本机工作台。")
        elif args.command == "stop":
            stop(settings)
        elif args.command == "doctor":
            return doctor(settings)
        elif args.command == "usage":
            return subprocess.call(
                [str(settings.cli_python), "-m", "h3_workbench.colab_cli", "usage"],
                env=subprocess_env(settings),
            )
        elif args.command == "release":
            require_instance(settings)
            response = local_json(settings.url + "/h3/release", {}, timeout=180)
            print(json.dumps(response))
            return 0 if response.get("released") else 1
        elif args.command == "status":
            state = local_json(settings.url + "/h3/status")
            print(json.dumps({k: state.get(k) for k in (
                "stage", "authorized", "gpu_session", "release_confirmed", "workbench_version",
            )}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        return 1
