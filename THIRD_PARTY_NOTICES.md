# 第三方声明

本仓库的集成代码采用 GPL-3.0-only。模型权重与外部依赖不因本仓库许可证而改变其授权条件。

| 组件 | 来源与版本 | 处理方式 |
|---|---|---|
| ComfyUI | [Comfy-Org/ComfyUI](https://github.com/Comfy-Org/ComfyUI)，提交 `8f04fd6ecf4a548937971e840af2a985529fab76` | GPL-3.0；安装时下载，不随仓库分发源码副本 |
| H3 工作流模板 | [Comfy-Org/workflow_templates](https://github.com/Comfy-Org/workflow_templates)，提交 `59a3b48bf369c5a15f29a1308bae3e279fa15505` | MIT；分发两个未修改模板，安装时派生省额度预设；许可证保留在 `third_party/` |
| google-colab-cli | [googlecolab/google-colab-cli](https://github.com/googlecolab/google-colab-cli)，0.7.4 | Apache-2.0；通过 Python 包安装，未复制其源码 |
| jupyter-kernel-client | [datalayer/jupyter-kernel-client](https://github.com/datalayer/jupyter-kernel-client)，0.9.0 | BSD-3-Clause；由 CLI 固定依赖安装，工作台在自身子进程内加连接兼容层 |
| ComfyUI 前端 | comfyui-frontend-package 1.55.18 | 安装为依赖，遵循其上游许可证 |
| H3 模型 | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) | 仅分发版本和校验清单；使用者应阅读模型仓库的许可证及模型卡 |
| Turbo LoRA | [lightx2v/Minimax-h3-Turbo](https://huggingface.co/lightx2v/Minimax-h3-Turbo) | 同上，不分发权重 |

模型和依赖的具体提交、文件名、大小与 SHA256 位于 `src/h3_workbench/resources/manifest.json`。Google Colab 的使用同时受其服务条款与资源分配规则约束。
