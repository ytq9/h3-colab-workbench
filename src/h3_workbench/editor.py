"""Launch the pinned native ComfyUI on loopback CPU, with a Colab executor."""
import atexit
import json
from pathlib import Path
import runpy
import signal
import sys

from .config import RESOURCES, load_settings
from .broker import ColabBroker
from .executor import executor_class


def main():
    settings = load_settings()
    LOCAL, COMFY, DRIVE = settings.runtime_dir, settings.comfy_dir, settings.project_dir
    for name in ("input", "output", "user"):
        (DRIVE / name).mkdir(parents=True, exist_ok=True)
    models = LOCAL / "editor-models"
    models.mkdir(exist_ok=True)
    sys.path.insert(0, str(COMFY))
    sys.argv = [str(COMFY / "main.py"), "--cpu", "--listen", "127.0.0.1", "--port", str(settings.port),
                "--disable-auto-launch", "--disable-partner-nodes", "--disable-all-custom-nodes",
                "--disable-assets", "--preview-method", "none", "--models-directory", str(models),
                "--input-directory", str(DRIVE / "input"), "--output-directory", str(DRIVE / "output"),
                "--user-directory", str(DRIVE / "user"), "--database-url", "sqlite:///" + str(LOCAL / "editor.db")]
    import comfy.options
    comfy.options.enable_args_parsing()
    import folder_paths
    manifest = json.loads((RESOURCES / "manifest.json").read_text())
    choices = {}
    for model in manifest["models"]:
        if "fl2va" in model["families"]:
            directory, name = model["destination"].split("/", 1)
            choices.setdefault(directory, set()).add(name)
    original_list = folder_paths.get_filename_list
    def editor_choices(name):
        canonical = {"unet": "diffusion_models", "clip": "text_encoders"}.get(name, name)
        return sorted(set(original_list(name)) | choices.get(canonical, set()))
    folder_paths.get_filename_list = editor_choices
    import execution
    import nodes
    import server
    from aiohttp import web
    from app.frontend_management import FrontendManager
    # This editor uses the user's H3 workflows. The separate stock template
    # gallery (and its ~500 MB demo media) is intentionally not installed.
    FrontendManager.template_asset_map = classmethod(lambda cls: {})
    FrontendManager.legacy_templates_path = classmethod(lambda cls: None)
    broker = ColabBroker(settings=settings)
    execution.PromptExecutor = executor_class(broker)
    original_routes = server.PromptServer.add_routes
    original_interrupt = nodes.interrupt_processing
    def interrupt(value=True):
        if value:
            broker.cancelled.set()
        return original_interrupt(value)
    nodes.interrupt_processing = interrupt

    @web.middleware
    async def local_only(request, handler):
        host = request.headers.get("Host")
        if host not in (f"127.0.0.1:{settings.port}", f"localhost:{settings.port}"):
            return web.json_response({"error": "仅支持本机访问"}, status=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("Origin") != "http://" + host:
                return web.json_response({"error": "请求来源不符"}, status=403)
        return await handler(request)

    def add_routes(instance):
        broker.emit = instance.send_sync
        instance.app.middlewares.insert(0, local_only)
        nodes.EXTENSION_WEB_DIRS["h3_colab_local"] = str(RESOURCES / "web")

        @instance.routes.get("/h3/status")
        async def status(request):
            return web.json_response(broker.status(), headers={"Cache-Control": "no-store"})

        @instance.routes.post("/h3/validate")
        async def validate(request):
            data = await request.json()
            import uuid
            result = await execution.validate_prompt(str(uuid.uuid4()), data["prompt"], None)
            return web.json_response({"valid": result[0], "error": result[1], "node_errors": result[3]})

        @instance.routes.get("/h3/auth")
        async def auth_page(request):
            return web.Response(text=(RESOURCES / "auth.html").read_text(), content_type="text/html",
                                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                         "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"})

        @instance.routes.post("/h3/auth/start")
        async def auth_start(request):
            try:
                broker.start_auth()
                return web.json_response({"ok": True})
            except Exception as error:
                return web.json_response({"error": str(error)}, status=409)

        @instance.routes.post("/h3/auth/code")
        async def auth_code(request):
            try:
                data = await request.json()
                broker.submit_auth_code(data.get("code"))
                return web.json_response({"ok": True})
            except Exception as error:
                return web.json_response({"error": str(error)}, status=400)

        @instance.routes.post("/h3/release")
        async def release(request):
            if instance.prompt_queue.get_tasks_remaining():
                return web.json_response({"error": "请先等待队列结束或点击中止。"}, status=409)
            import asyncio
            result = await asyncio.to_thread(broker.stop_gpu)
            return web.json_response({"released": result})

        original_routes(instance)
    server.PromptServer.add_routes = add_routes
    atexit.register(broker.shutdown)
    def terminate(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    runpy.run_path(str(COMFY / "main.py"), run_name="__main__")


if __name__ == "__main__":
    main()
