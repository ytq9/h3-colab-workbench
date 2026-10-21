# H3 参考工作流

参考模式可以把多张图片、视频片段和音频一起送入原生 `MiniMaxH3ReferenceToVideo`。Mac 负责编辑和素材管理，运行后仍由 Colab A100 推理，取回结果后释放。

## 安装与导入

先完成基础安装。已有工作台等队列结束、GPU 释放后执行：

```bash
.venv/bin/h3-workbench stop
.venv/bin/h3-workbench install-reference
.venv/bin/h3-workbench start
```

`install-reference` 只安装本机节点和 CPU 依赖，不申请 Colab、不下载模型。刷新网页，将自己持有的工作流 JSON 拖入画布，或放入 `项目目录/user/default/workflows/H3/` 后从工作流列表打开。

插件来自固定提交：KJNodes 提供 Set/Get 和画布预览，VideoHelperSuite 提供视频加载与输出，Goohaitools 提供编组开关。安装器只启用这些受管理的插件；KJ 和孤海仅安装所需原版文件及加载入口，不安装它们的无关节点依赖。

## 可选模型

参考组合清单位于 `src/h3_workbench/resources/reference.json`。以下组合共约 **55.89 GB**，模型在 Colab 下载，本机菜单提供文件名供编辑。

| 用途 | 文件 |
|---|---|
| 主模型 | `Cobijada_H3_Hybrid_Uncensored_pruned_hybrid_int8_convrot_pruned.safetensors` |
| 文本编码器 | `qwen3vl_32b_minimax_h3_int8_convrot.safetensors` |
| 视频 VAE | `minimax_h3_video_vae_fp16.safetensors` |
| 音频 VAE | `minimax_h3_audio_vae_fp32.safetensors` |
| Turbo LoRA | `minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors` |
| 画布预览 VAE | `taeh3.safetensors` |

程序以实际执行分支上的模型文件选择为准，不根据工作流备注中的下载链接自动替换模型。不会同时下载基础和参考两整套模型。未知模型在申请 GPU 前报错；不能仅填入任意文件名就使用它。

更换为当前会话未加载的模型组合时，工作台会先释放旧会话，再准备新 GPU。将使用相同模型的任务连续排队可减少准备次数。

## 素材与控制

工作流 JSON 通常只保存素材文件名。原作者的图片、视频、音频没有随文件提供时，需要在启用的加载节点上传自己的素材。暂不使用的参考编组保持旁路状态；旁路 LoRA 也不会下载或执行。

- 图片使用 `LoadImage`，每份最多 100 MB。
- 音频使用 `LoadAudio`，每份最多 512 MB。
- 视频使用 `VHS_LoadVideo` 或 `VHS_LoadVideoFFmpeg`，每份最多 1 GB。

上传到本机后，在对应编组开关中启用输入。参考视频的帧率、截取长度和抽帧由原工作流中的 VHS 参数决定。程序在申请 GPU 前建立素材快照，后续修改画布不会改变已排队任务的输入。

VHS 输出节点请保持 **save_output 开启**。最终 MP4、启用时的工作流 PNG 会回到项目的 `output/video/<任务编号>/`。KJ 画布预览经同一 Colab 连接回传，不需要额外登录或公网隧道；网络中断可能影响实时预览，最终文件仍按任务结果取回。

## 验证范围

本机原生导出与参数校验、插件注册、素材快照、模型选择、预览转发、文件取回和释放逻辑已验证。参考组合尚未完成 A100 实际推理验收，不能据此保证所有分辨率、时长或参考素材组合都适合 40 GB 显存。安装、下载、加载模型也消耗 Colab 额度。

第三方工作流的节点数量、布局、提示词和参数由其文件决定，安装插件不会重写工作流。仓库不分发作者的个人工作流、提示词或媒体。
