# 贡献

使用 Python 3.12。基础测试不需要 Google 账号、Colab、模型或 GPU：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install '.[colab]'
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_release.py
```

修改源码后重新安装包，或使用 `PYTHONPATH=src` 执行开发检查。运行中的本机后台不会热更新。

已有本机 CPU 工作台运行时，可执行 `.venv/bin/python scripts/smoke_local.py` 验证原生节点、工作流与接口来源检查；该脚本不提交生成。

生命周期修改应验证：未点击运行时零分配；失败和取消后的释放；未知状态不报成功；文件重试不重新推理；其他 Colab 会话不被停止。测试使用临时目录和假 CLI，禁止在 CI 中放入真实凭据。

传输回归测试使用已固定的 CLI/Jupyter 依赖，模拟延迟握手和断线，不连接真实云端。缺少 colab extra 时这组测试会跳过；发布和 CI 必须安装该 extra 并确认没有跳过。

依赖升级需同时检查 ComfyUI 固定提交、前端、模型节点和工作流。更新 manifest 时记录来源，重新计算校验值。CPU 的依赖快照不能直接作为 Colab CUDA 环境的锁文件。

报告验证时区分单元测试、CPU 界面测试、传输测试和真实 GPU 视频。明确记录未验证路径。
