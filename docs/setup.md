# 安装、配置与迁移

## 前提

- Apple Silicon Mac，macOS 14 或更高，Python 3.12，Git，Google Chrome。
- Google 账号能实际分配 Colab A100，且有可用计算额度。是否可分配取决于 Colab；订阅名称本身不能替代实际查询。
- 本机要有 CPU 编辑依赖所需磁盘空间。约 42 GB 的模型下载发生在 Colab。

先按根 README 安装 `.venv` 中的命令行工具，再运行 `init`、`install`、`start`。安装只建立本机环境；首次授权只建立 Google 连接；点击 ComfyUI 的运行按钮才申请 GPU。

## 配置

Mac 默认配置路径：

```text
~/Library/Application Support/H3 Workbench/config.json
```

用环境变量 `H3_CONFIG` 指向另一份配置。完整配置由 `init` 生成，字段为：

| 字段 | 用途 |
|---|---|
| `account_email` | 允许使用的 Google 账号；每次申请 GPU 前重新核对 |
| `project_dir` | 素材、工作流和视频；可以是 Drive 同步目录 |
| `runtime_dir` | ComfyUI 源码、CPU 虚拟环境、索引数据库、进程记录 |
| `credential_dir` | OAuth 和 Colab 会话状态；必须在项目目录之外 |
| `cli_python` | 安装了固定版本 Colab CLI 的虚拟环境 Python |
| `port` | 本机端口，默认 8190 |
| `browser` | Mac 浏览器应用名，默认 Google Chrome |

配置文件不包含 OAuth token。账号信息也无需提交到 Git；`config.example.json` 只是例子。

更改配置前结束当前任务，执行 `stop`。保留配置中的虚拟环境 Python 路径，不要把它替换成系统 Python。

## Google 授权

工作台使用官方 `google-colab-cli` 的 OAuth 流程，范围为识别账号、读取邮箱和管理 Colab。无需提供 Google Cloud 服务账号，不请求 Drive API 范围。

本机连接页会显示配置的账号。用户在 Google 页面亲自完成授权；只有点击连接按钮才允许交互授权，后台命令遇到授权失效会停止并提示重新连接。

凭据和 CLI 历史保存在独立的私有目录，权限为目录 0700、文件 0600。不要将该目录放进 Drive、工作流目录或仓库。

## 使用 Drive

将 `project_dir` 设置到 Google Drive for desktop 的同步路径即可。本机程序把文件写进该目录，云端同步由 Drive 客户端完成。文件已落盘不等于 Drive 已完成上传。

Colab 不需要挂载 Drive；远程输入输出通过官方 CLI 传输。

## 已有项目迁移

保留原始项目文件，配置 `project_dir` 指向原目录。安装器只补充缺少的示例工作流，不覆盖同名工作流。已有视频、输入图片、任务快照均继续保留。

`runtime_dir` 默认使用新目录。如复用旧 ComfyUI 环境，该目录必须包含相同固定版本的 `source-receipt.json`，否则安装器会拒绝覆盖。旧进程与新版本不能同时占用 8190；迁移验证可用独立配置和 8191 端口。

## 更新与卸载

更新前等待队列完成、确认 GPU 已释放，再停止本机后台：

```bash
git pull --ff-only
.venv/bin/python -m pip install --no-deps .
.venv/bin/h3-workbench install
.venv/bin/h3-workbench start
```

卸载时先停止后台并确认云端没有残留会话。源码、运行环境、凭据和用户项目目录相互独立；根据需要保留视频和工作流。可在 Google 账号的第三方连接中撤销授权。
