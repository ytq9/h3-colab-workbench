"""Run the original ComfyUI queue on on-demand Colab GPUs, not on the Mac."""
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid

from .worker import setup_script, job_script, lease_script
from .config import load_settings, subprocess_env
from . import __version__


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def safe_relative(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("文件路径必须位于项目目录内。")
    return path


def redact_diagnostic(text):
    text = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~-]+", r"\1<REDACTED>", text)
    text = re.sub(r"eyJ[A-Za-z0-9_.-]{20,}", "<REDACTED>", text)
    text = re.sub(
        r"""(?i)((?:access_token|refresh_token|id_token|client_secret|authorization)["']?\s*[:=]\s*["']?)[^"'\s,}]+""",
        r"\1<REDACTED>", text,
    )
    return re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?<REDACTED>", text)


class ColabBroker:
    def __init__(self, project=None, local=None, settings=None):
        self.settings = settings or load_settings()
        self.project = Path(project or self.settings.project_dir)
        self.local = Path(local or self.settings.runtime_dir)
        self.private = self.settings.credential_dir
        self.cli = [str(self.settings.cli_python), "-m", "h3_workbench.colab_cli"]
        self.stage = "本机 CPU 编辑 · GPU 未启动"
        self.session = None
        self.email = None
        self.authorized = False
        self.cancelled = threading.Event()
        self.emit = lambda *a, **k: None
        self._stop_lock = threading.Lock()
        self._auth_lock = threading.Lock()
        self.auth_url = None
        self.auth_process = None
        self.auth_message = "首次连接 Google 后，生成时才能使用你的 Colab 额度。"
        self.release_confirmed = True
        self.allocated_confirmed = False
        self.worker_ready = False
        self.active_job = False
        self.local.mkdir(parents=True, exist_ok=True)
        self.owned_record = self.private / f"owned-{self.settings.instance_id}.json"
        if self.owned_record.is_file():
            previous = json.loads(self.owned_record.read_text())
            if not re.fullmatch(r"h3-[0-9a-f]{32}", previous.get("session", "")):
                raise RuntimeError("本机 GPU 会话记录无效；请先核对 Colab 管理会话。")
            self.session = previous["session"]
            self.allocated_confirmed = previous.get("allocated_confirmed") is True
            self.release_confirmed = False
            self.stage = "GPU 释放未确认：发现上次进程留下的会话"
        if (self.private / "token.json").is_file() and (self.private / "identity.json").is_file():
            try:
                self.email = json.loads((self.private / "identity.json").read_text()).get("email")
                self.authorized = self.email == self.settings.account_email
                if self.authorized:
                    self.auth_message = "已保存 Google 连接；每次申请 GPU 前会重新核对账号和额度。"
            except (ValueError, OSError):
                pass

    def persist_owned_session(self):
        self.private.mkdir(parents=True, exist_ok=True)
        self.private.chmod(0o700)
        temporary = self.owned_record.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "session": self.session, "allocated_confirmed": self.allocated_confirmed,
        }))
        temporary.chmod(0o600)
        temporary.replace(self.owned_record)

    def status(self):
        return {"stage": self.stage, "authorized": self.authorized, "email": self.email,
                "gpu_session": bool(self.session), "release_confirmed": self.release_confirmed,
                "auth_url": self.auth_url, "auth_message": self.auth_message,
                "expected_account": self.settings.account_email,
                "instance_id": self.settings.instance_id, "workbench_version": __version__}

    def set_stage(self, stage):
        self.stage = stage
        try:
            self.emit("h3_state", {"stage": stage, "authorized": self.authorized,
                                   "gpu_session": bool(self.session),
                                   "release_confirmed": self.release_confirmed})
        except Exception:
            # Closing the local browser/server must not prevent GPU cleanup.
            pass

    def command(self, args, timeout=120, cancelable=True):
        env = dict(subprocess_env(self.settings), PYTHONUNBUFFERED="1", NO_COLOR="1",
                   COLAB_CLI_REQUEST_TIMEOUT="45", COLAB_CLI_ASSIGN_RETRIES="1")
        proc = subprocess.Popen(self.cli + list(args), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, env=env)
        lines, stream = [], queue.Queue()
        def read():
            for line in proc.stdout:
                stream.put(line)
            stream.put(None)
        threading.Thread(target=read, daemon=True).start()
        deadline = time.monotonic() + timeout
        ended = False
        try:
            while not ended or proc.poll() is None:
                if time.monotonic() > deadline:
                    raise TimeoutError("Colab 操作超时。")
                if cancelable and self.cancelled.is_set():
                    raise RuntimeError("已取消本次生成。")
                try:
                    line = stream.get(timeout=0.2)
                except queue.Empty:
                    continue
                if line is None:
                    ended = True
                    continue
                lines.append(line)
                if len(lines) > 2000:
                    lines = lines[-1000:]
                if "H3_STAGE " in line:
                    self.set_stage(line.split("H3_STAGE ", 1)[1].strip()[:100])
            result = "".join(lines)
            if proc.returncode:
                if "H3_AUTH_REQUIRED" in result or "authorize colab-cli" in result:
                    self.authorized = False
                    raise RuntimeError("请先在本机工作台点击“连接 Google”。")
                raise RuntimeError(f"Colab 操作失败（{args[0]}）；详情见本机私有诊断日志。未自动重试生成。")
            return result
        except Exception as error:
            # Keep the original upload/generation failure when later cleanup
            # also fails. Raw credentials never enter ordinary project logs.
            log_dir = self.private / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            self.private.chmod(0o700)
            log_dir.chmod(0o700)
            operation = re.sub(r"[^a-zA-Z0-9_]", "", str(args[0])) or "operation"
            log = log_dir / f"{operation}-{time.time_ns()}-{uuid.uuid4().hex[:6]}.log"
            log.write_text(redact_diagnostic("".join(lines) + "\n" + type(error).__name__))
            log.chmod(0o600)
            raise
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            proc.stdout.close()

    def transfer(self, args, timeout):
        if args[0] not in ("upload", "download"):
            raise ValueError("Only idempotent file transfer may be retried.")
        lease = self.local / "remote-transfer-lease.py"
        lease.write_text(lease_script(min(600, timeout * 2 + 60)))
        self.command(["exec", "-s", self.session, "-f", str(lease), "--timeout", "30"], timeout=40)
        for attempt in range(2):
            try:
                return self.command(args, timeout=timeout)
            except (RuntimeError, TimeoutError, OSError):
                if attempt or self.cancelled.is_set() or not self.authorized:
                    raise
                self.set_stage("文件传输中断，正在重试一次")
                if self.cancelled.wait(2):
                    raise RuntimeError("已取消本次生成。")

    def check_identity(self):
        output = self.command(["--h3-check"], timeout=60, cancelable=False)
        line = next((s for s in output.splitlines() if s.startswith("H3_IDENTITY ")), None)
        if not line:
            raise RuntimeError("无法确认 Google 账号，未申请 GPU。")
        info = json.loads(line[len("H3_IDENTITY "):])
        self.email = info.get("email")
        self.authorized = self.email == self.settings.account_email and info.get("correct_account") is True
        if not self.authorized:
            raise RuntimeError(f"请连接 {self.settings.account_email}；未申请 GPU。")
        return True

    def start_auth(self):
        with self._auth_lock:
            if self.active_job or (self.session and self.worker_ready):
                raise RuntimeError("请等待当前 GPU 会话结束后再连接账号。")
            if self.auth_process is not None and self.auth_process.poll() is None:
                return
            self.auth_url = None
            self.authorized = False
            self.auth_message = "正在准备 Google 授权链接；不会启动 GPU。"
            self.auth_process = subprocess.Popen(
                self.cli + ["--h3-login"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                env=subprocess_env(self.settings),
            )
            process = self.auth_process
        def consume():
            try:
                for line in process.stdout:
                    if line.strip().startswith("https://accounts.google.com/"):
                        self.auth_url = line.strip()
                        self.auth_message = f"打开 Google 授权页，选择 {self.settings.account_email}；将 Google 显示的授权码填回本页。"
                    if "H3_IDENTITY " in line:
                        info = json.loads(line.split("H3_IDENTITY ", 1)[1])
                        self.email = info.get("email")
                        self.authorized = self.email == self.settings.account_email and info.get("correct_account") is True
                process.wait()
                if self.authorized:
                    self.auth_message = "账号已连接。接下来点击 ComfyUI 的运行按钮才会申请 GPU。"
                    self.auth_url = None
                else:
                    self.auth_message = f"账号连接未完成或不是 {self.settings.account_email}；可重新连接。"
            finally:
                process.stdout.close()
        threading.Thread(target=consume, daemon=True).start()
        def expire():
            if process.poll() is None:
                process.terminate()
        timer = threading.Timer(600, expire)
        timer.daemon = True
        timer.start()

    def submit_auth_code(self, code):
        if not isinstance(code, str) or not 10 <= len(code) <= 4096 or "\n" in code or "\r" in code:
            raise ValueError("授权码格式不正确。")
        with self._auth_lock:
            if self.auth_process is None or self.auth_process.poll() is not None:
                raise RuntimeError("授权已过期，请重新连接。")
            self.auth_process.stdin.write(code.strip() + "\n")
            self.auth_process.stdin.flush()
        self.auth_message = "正在向 Google 完成授权…"

    def ensure_gpu(self):
        if self.session:
            if self.worker_ready and not self.release_confirmed:
                return
            raise RuntimeError("上一次 GPU 会话状态未确认，请先到 Colab 检查。")
        self.check_identity()
        usage = self.command(["usage"], timeout=90)
        balance = re.search(r"(?:Current balance|Balance):\s*([\d,.]+)", usage, re.I)
        if not balance or float(balance[1].replace(",", "")) <= 0:
            raise RuntimeError("无法确认现有 Colab 额度充足，未申请 GPU。")
        self.session = "h3-" + uuid.uuid4().hex
        self.release_confirmed = False
        self.persist_owned_session()
        self.set_stage("正在申请 Colab A100")
        self.command(["new", "-s", self.session, "--gpu", "A100", "--high-mem"], timeout=240)
        self.allocated_confirmed = True
        self.persist_owned_session()
        script = self.local / "remote-setup.py"
        script.write_text(setup_script())
        self.command(["exec", "-s", self.session, "-f", str(script), "--timeout", "1200"], timeout=1300)
        self.worker_ready = True

    def stop_gpu(self):
        with self._stop_lock:
            if not self.session:
                return self.release_confirmed
            name = self.session
            self.worker_ready = False
            self.set_stage("正在释放 Colab GPU")
            confirmed = False
            try:
                output = self.command(["stop", "-s", name], timeout=90, cancelable=False)
                confirmed = ("Session terminated." in output or
                             ("not found" in output and self.allocated_confirmed))
            except Exception:
                pass
            if not confirmed and self.allocated_confirmed:
                try:
                    # The server may already have reclaimed the runtime while
                    # the CLI still has its cached record, making stop fail
                    # with 404. A successful live zero-assignment response
                    # confirms that our runtime cannot still be consuming CU.
                    usage = self.command(["usage"], timeout=60, cancelable=False)
                    active = re.search(r"(?im)^\s*Active assignments:\s*(\d+)\s*$", usage)
                    confirmed = active is not None and int(active[1]) == 0
                except Exception:
                    pass
            if confirmed:
                self.owned_record.unlink(missing_ok=True)
                self.session = None
                self.release_confirmed = True
                self.allocated_confirmed = False
                self.set_stage("本机 CPU 编辑 · GPU 已释放")
                return True
            self.release_confirmed = False
            self.set_stage("GPU 释放未确认，请在 Colab 管理会话检查")
            return False

    def prepare_job(self, prompt, prompt_id, extra, partial_targets=None):
        reachable = set()
        pending = list(partial_targets if partial_targets is not None else prompt)
        while pending:
            ident = str(pending.pop())
            if ident in reachable or ident not in prompt:
                continue
            reachable.add(ident)
            for value in prompt[ident].get("inputs", {}).values():
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                    pending.append(value[0])
        if not any(prompt[n].get("class_type") == "MiniMaxH3ImageToVideo" for n in reachable):
            raise ValueError("此工作台目前支持 H3 文生和图生模板。")
        job_id = str(uuid.UUID(prompt_id))
        folder = self.project / "jobs" / job_id
        folder.mkdir(parents=True, exist_ok=False)
        request = {"prompt": copy.deepcopy(prompt), "prompt_id": job_id, "client_id": job_id,
                   "extra_data": {"extra_pnginfo": extra.get("extra_pnginfo", {})}}
        if partial_targets is not None:
            request["partial_execution_targets"] = list(partial_targets)
        assets = []
        for ident, node in request["prompt"].items():
            if ident not in reachable or node.get("class_type") != "LoadImage":
                continue
            name = node["inputs"].get("image")
            if not isinstance(name, str):
                raise ValueError("图片输入必须来自工作台上传目录。")
            relative = safe_relative(name)
            source = (self.project / "input" / relative).resolve()
            source.relative_to((self.project / "input").resolve())
            if not source.is_file() or source.stat().st_size > 100 * 1024 * 1024:
                raise ValueError("输入图片缺失或超过 100 MB。")
            new_name = job_id + "-" + str(ident).replace(":", "_") + source.suffix
            # Node IDs are untrusted; never let them form arbitrary file paths.
            new_name = re.sub(r"[^A-Za-z0-9_.-]", "_", new_name)
            target = folder / new_name
            shutil.copyfile(source, target)
            node["inputs"]["image"] = new_name
            assets.append((target, "/content/h3-worker-files/input/" + new_name))
        request_path = folder / "request.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False))
        snapshots = self.project / "executed-workflows"
        snapshots.mkdir(exist_ok=True)
        (snapshots / (job_id + "-api.json")).write_text(json.dumps(prompt, ensure_ascii=False))
        ui = extra.get("extra_pnginfo", {}).get("workflow")
        if ui:
            (snapshots / (job_id + "-ui.json")).write_text(json.dumps(ui, ensure_ascii=False))
        return folder, request_path, assets

    def retrieve_result(self, folder, result):
        mapping = {}
        for item in result["files"]:
            subfolder = str(safe_relative(item["subfolder"])) if item["subfolder"] else ""
            filename = str(safe_relative(item["filename"]))
            if "/" in filename:
                raise ValueError("生成结果文件名无效。")
            expected = "/content/h3-worker-files/output/" + ((subfolder + "/") if subfolder else "") + filename
            if item["remote_path"] != expected or item["size"] > 2 * 1024**3:
                raise ValueError("生成结果路径或大小异常。")
            local_subfolder = str(PurePosixPath("video") / folder.name / subfolder)
            target = self.project / "output" / local_subfolder / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_suffix(target.suffix + ".part")
            self.transfer(["download", "-s", self.session, item["remote_path"], str(partial)], timeout=120)
            if partial.stat().st_size != item["size"] or digest(partial) != item["sha256"]:
                raise RuntimeError("视频传输校验失败，未将其标记为完成。")
            partial.replace(target)
            mapping[(subfolder, filename)] = local_subfolder
        def rewrite(value):
            if isinstance(value, dict):
                if "filename" in value and value.get("type") == "output":
                    key = (value.get("subfolder", ""), value["filename"])
                    if key not in mapping:
                        raise ValueError("结果中的文件尚未下载。")
                    value["subfolder"] = mapping[key]
                for child in value.values():
                    rewrite(child)
            elif isinstance(value, list):
                for child in value:
                    rewrite(child)
        rewrite(result["outputs"])
        return result

    def execute(self, prompt, prompt_id, extra, queue_has_more, partial_targets=None):
        self.cancelled.clear()
        self.active_job = True
        failed = True
        try:
            folder, request, assets = self.prepare_job(prompt, prompt_id, extra, partial_targets)
            self.ensure_gpu()
            self.set_stage("正在传送素材")
            for source, target in assets:
                self.transfer(["upload", "-s", self.session, str(source), target], timeout=60)
            remote_request, remote_result = "/content/" + folder.name + ".json", "/content/" + folder.name + "-result.json"
            self.transfer(["upload", "-s", self.session, str(request), remote_request], timeout=60)
            script = self.local / "remote-job.py"
            script.write_text(job_script(remote_request, remote_result))
            self.command(["exec", "-s", self.session, "-f", str(script), "--timeout", "1300"], timeout=1350)
            result_path = folder / "result.json"
            self.transfer(["download", "-s", self.session, remote_result, str(result_path)], timeout=60)
            result = json.loads(result_path.read_text())
            self.set_stage("正在取回视频并保存到 Drive 同步目录")
            result = self.retrieve_result(folder, result)
            result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
            failed = not result.get("success")
            return result
        finally:
            try:
                if failed or self.cancelled.is_set() or not queue_has_more():
                    self.stop_gpu()
            finally:
                self.active_job = False

    def shutdown(self):
        self.cancelled.set()
        self.stop_gpu()
        if self.auth_process is not None and self.auth_process.poll() is None:
            self.auth_process.terminate()
