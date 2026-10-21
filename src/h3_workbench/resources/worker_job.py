import json, time, urllib.request
from pathlib import Path
import threading


def h3_start_events(client_id):
    """Relay only progress/preview events over the existing Colab exec stream."""
    import websocket
    from urllib.parse import urlencode
    connection = websocket.create_connection(
        "ws://127.0.0.1:8188/ws?" + urlencode({"clientId": client_id}), timeout=3,
        http_no_proxy=["127.0.0.1", "localhost"],
    )
    connection.settimeout(1)
    stopped = threading.Event()
    def consume():
        while not stopped.is_set():
            try:
                message = connection.recv()
                if not isinstance(message, str) or len(message) > 8 * 1024**2:
                    continue
                event = json.loads(message)
                if event.get("type") not in {"progress", "executing", "kj_preview_override"}:
                    continue
                data = event.get("data", {})
                if not isinstance(data, dict) or data.get("prompt_id", client_id) != client_id:
                    continue
                event["job_id"] = client_id
                print("H3_EVENT " + json.dumps(event, ensure_ascii=False), flush=True)
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                return
    listener = threading.Thread(target=consume, daemon=True)
    listener.start()
    def stop():
        stopped.set()
        connection.close()
        listener.join(timeout=2)
    return stop

def h3_job_json(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        "http://127.0.0.1:8188" + path, data=data,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read()
        return json.loads(data) if data else {}

def h3_worker_job(request_file, result_file):
    global H3_WORKER_BUSY, H3_WORKER_TOUCHED
    H3_WORKER_BUSY = True
    print("H3_STAGE 工作流已送达，正在提交生成", flush=True)
    result = {"success": False, "outputs": {}, "meta": {}, "files": [], "messages": []}
    stop_events = lambda: None
    try:
        request = json.loads(Path(request_file).read_text())
        if request.pop("stream_events", False):
            try:
                stop_events = h3_start_events(request["client_id"])
            except Exception:
                print("Live preview connection unavailable; result retrieval remains active.", flush=True)
        accepted = h3_job_json("/prompt", request)
        prompt_id = accepted["prompt_id"]
        deadline = time.monotonic() + 20 * 60
        print("H3_STAGE 正在生成", flush=True)
        while time.monotonic() < deadline:
            history = h3_job_json("/history/" + prompt_id)
            if prompt_id in history:
                entry = history[prompt_id]
                break
            time.sleep(2)
        else:
            h3_job_json("/interrupt", {})
            raise RuntimeError("本次生成超过 20 分钟上限，已请求中止。")
        result["success"] = entry.get("status", {}).get("status_str") == "success"
        result["outputs"] = entry.get("outputs", {})
        result["meta"] = entry.get("meta", {})
        result["messages"] = entry.get("status", {}).get("messages", [])
        def walk(value):
            if isinstance(value, dict):
                yield value
                for child in value.values():
                    yield from walk(child)
            elif isinstance(value, list):
                for child in value:
                    yield from walk(child)
        found = set()
        for item in walk(result["outputs"]):
            if "filename" not in item or item.get("type") != "output":
                continue
            subfolder, filename = item.get("subfolder", ""), item["filename"]
            key = (subfolder, filename)
            if key in found:
                continue
            found.add(key)
            filenames = [filename]
            if item.get("workflow") and isinstance(item["workflow"], str):
                filenames.append(item["workflow"])
            for file in filenames:
                file_key = (subfolder, file)
                if file != filename and file_key in found:
                    continue
                path = (OUTPUT_DIR / subfolder / file).resolve()
                path.relative_to(OUTPUT_DIR.resolve())
                if not path.is_file():
                    # VHS keeps a workflow filename even when its optional
                    # metadata-image setting is off. The video is still valid.
                    if file != filename:
                        item.pop("workflow", None)
                        continue
                    raise RuntimeError("生成结果文件不存在。")
                found.add(file_key)
                result["files"].append({"filename": file, "subfolder": subfolder,
                                        "size": path.stat().st_size, "remote_path": str(path)})
    except Exception as error:
        result["success"] = False
        result["error"] = str(error)[:2000]
    finally:
        try:
            stop_events()
        except Exception:
            pass
        Path(result_file).write_text(json.dumps(result, ensure_ascii=False))
        H3_WORKER_TOUCHED = time.monotonic()
        H3_WORKER_BUSY = False
        print("H3_JOB_FINISHED", flush=True)
