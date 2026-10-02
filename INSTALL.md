# 安装

电脑需要已安装并使用过 Codex。Windows 提供 x64 免安装包；macOS 使用 Python 源码启动，属于未在 Mac 实机验证的实验性支持。

## Windows

从 [Releases 下载页](https://github.com/Yamazzzx/codex-speed-panel/releases) 下载文件名带 `windows-x64` 的 ZIP，完整解压后双击 `CodexSpeedPanel.exe`。无需 Python、管理员权限或 API Key。

程序启动时检查数据目录；从网页顶部选择要查看的聊天。目录不可用时，在“数据目录”中填写正确目录后点击“使用此目录”。

用完点击“关闭监测”或运行 `关闭监测.cmd`。卸载时关闭面板，再删除解压目录。

## macOS 源码方式

1. 安装或确认已有 Python 3.10 或更新版本，可运行 `python3 --version` 检查。
2. 下载源码 ZIP，解压后进入 `codex-speed-panel` 目录。
3. 在终端运行 `python3 server.py`，浏览器打开 `http://127.0.0.1:19876/`。前台运行时用 Ctrl+C 停止。
4. 也可用 `start.command` 启动后台并打开浏览器，使用 `stop.command` 停止。脚本没有执行权限时，先运行 `chmod +x start.command stop.command`。

目录发现与读取代码采用 Python 标准库；候选目录逻辑有模拟测试，启动脚本有语法检查。实际 Mac 上的 Python 环境、目录权限、Codex 数据格式及双击行为仍需实测。遵守系统安全提示，不自动移除隔离属性或绕过安全拦截。

## 让 Codex 安装

将仓库链接交给 Codex，并输入：

> 请按 INSTALL.md 安装适合当前系统的版本，校验下载包后启动，并在侧栏打开面板。

执行安装时：

1. 检查系统。Windows x64 使用正式 Release 的 `windows-x64` ZIP；macOS 使用同一 Release 的 `source` ZIP，并先确认 Python 3.10+ 可用。缺少发布包或运行环境时报告缺失项，不替换来源。
2. 下载对应 ZIP 和 `SHA256SUMS.txt`，校验 SHA-256；校验失败时停止。
3. 解压到用户指定目录。Windows 默认使用 `%LOCALAPPDATA%\CodexPulse`，macOS 默认使用 `~/.local/share/codex-speed-panel`。保留程序、文档、停止脚本和许可证。
4. 默认端口已有服务时，先确认 `/api/health` 的 `app` 为 `codex-speed-panel`，再用 `POST /api/stop` 关闭该面板；不结束其他服务。
5. Windows 隐藏启动 `CodexSpeedPanel.exe --no-browser`；macOS 运行 `python3 launch.pyw --no-browser`。检查 `http://127.0.0.1:19876/api/health` 的应用标识，再打开本地主页。
6. 检查 `/api/source` 是否找到可用数据；需要手动目录时由用户提供，不搜索整个磁盘。网页中手动选择聊天。
7. 报告安装目录和实际检查结果。Mac 上本次通过的检查应单独说明，不能把模拟测试称为 Mac 实测。

只安装本面板，不修改 Codex 配置，不创建开机启动或计划任务，不读取或上传登录文件、密钥和聊天原文。卸载保留 Codex 自身的数据。
