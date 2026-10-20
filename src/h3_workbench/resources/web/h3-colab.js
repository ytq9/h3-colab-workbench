import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

app.registerExtension({
  name: "H3.LocalColab",
  async setup() {
    const panel = document.createElement("div");
    panel.style.cssText = "position:fixed;bottom:12px;left:72px;z-index:9000;padding:10px 16px;border:1px solid #52617a;border-radius:10px;background:#182333;color:#f4f7ff;font:13px system-ui;box-shadow:0 4px 12px #0004;display:flex;align-items:center;gap:14px";
    const label = document.createElement("span");
    label.textContent = "本机 CPU 编辑 · GPU 未启动";
    const connect = document.createElement("a");
    connect.href = "/h3/auth";
    connect.target = "_blank";
    connect.rel = "noopener noreferrer";
    connect.textContent = "连接 Google";
    connect.style.cssText = "color:#a8ceff;text-decoration:underline";
    const retry = document.createElement("button");
    retry.textContent = "重试释放 GPU";
    retry.style.cssText = "display:none;color:#fff;background:#724320;border:0;padding:4px 8px;border-radius:5px";
    retry.onclick = async () => {
      retry.disabled = true;
      try { await api.fetchApi("/h3/release", {method:"POST"}); }
      finally { retry.disabled = false; }
    };
    panel.append(label, connect, retry);
    document.body.append(panel);
    const refresh = async () => {
      try {
        const response = await api.fetchApi("/h3/status");
        if (!response.ok) return;
        const status = await response.json();
        label.textContent = status.stage;
        connect.textContent = status.authorized ? "Google 已连接" : "连接 Google";
        retry.style.display = !status.release_confirmed && /释放未确认/.test(status.stage) ? "" : "none";
      } catch { label.textContent = "本机工作台连接中…"; }
    };
    await refresh();
    setInterval(refresh, 5000);
  }
});
