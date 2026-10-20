"""Make small presets without changing the official subgraph implementation."""
import copy
import json
from pathlib import Path
import uuid


def make_presets(upstream):
    presets = {}
    for kind, name, main_id in (
        ("t2v", "01_文生_省额度.json", 140),
        ("i2v", "02_图生_省额度.json", 105),
    ):
        graph = json.loads((Path(upstream) / f"video_minimax_h3_{kind}.json").read_text())
        graph = copy.deepcopy(graph)
        # I2V's two unconnected helper nodes are not on the output path.
        removed = {n["id"] for n in graph["nodes"]
                   if n["type"] == "MarkdownNote" or n["id"] in (119, 120)}
        graph["nodes"] = [n for n in graph["nodes"] if n["id"] not in removed]
        graph["links"] = [link for link in graph["links"] if link[1] not in removed and link[3] not in removed]
        main = next(n for n in graph["nodes"] if n["id"] == main_id)
        main["title"] = "写提示词 · 5 秒视频与声音" if kind == "t2v" else "写动作与声音 · 5 秒图生视频"
        values = main["widgets_values"]
        assert len(values) == 13
        values[0] = (
            "写实电影镜头。夜晚湖面上一盏暖黄色纸灯缓缓漂浮，水面映出微弱倒影。"
            "镜头缓慢向前推进，保持平稳。轻微水声和夜风，无配乐。单镜头，无字幕、文字或水印。"
            if kind == "t2v" else
            "以输入图片作为第一帧，保持主体外观和场景一致。镜头缓慢向前推进，"
            "主体轻微自然运动。柔和的环境声，无配乐。单镜头，无字幕、文字或水印。"
        )
        values[1:5] = [608, 352, 5, 42]
        values[9], values[11], values[12] = True, 1.0, 8
        main["pos"] = [360 if kind == "t2v" else 700, 60]
        main["size"] = [480, 660]
        selector = next(n for n in graph["nodes"] if n["type"] == "ResolutionSelector")
        selector["title"] = "画幅 · 小规格预览"
        selector["widgets_values"] = ["16:9 (Widescreen)", 0.2, 32]
        selector["pos"] = [40 if kind == "t2v" else 390, 60]
        selector["size"] = [270, 170]
        save = next(n for n in graph["nodes"] if n["type"] == "SaveVideo")
        save["title"] = "视频结果 · 自动保存到项目目录"
        save["pos"] = [900 if kind == "t2v" else 1240, 60]
        save["size"] = [540, 420]
        save["widgets_values"][0] = "video/H3_文生" if kind == "t2v" else "video/H3_图生"
        if kind == "i2v":
            load = next(n for n in graph["nodes"] if n["type"] == "LoadImage")
            load["title"] = "上传自己的图片"
            load["widgets_values"][0] = ""
            load["pos"], load["size"] = [40, 60], [300, 440]
        graph["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, "h3-colab/presets/v1/" + name))
        graph["revision"] = 0
        graph["groups"] = []
        graph.setdefault("extra", {})["ds"] = {"scale": 0.65 if kind == "t2v" else 0.55, "offset": [20, 40]}
        presets[name] = graph
    return presets
