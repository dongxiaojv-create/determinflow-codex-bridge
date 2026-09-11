# 0.3.1 发布验证

日期：2026-09-11。此版本为 macOS Apple Silicon 预览版。

## 已验证

- 0.3.1：`tests/check_selection.py` 经真实任务 HTTP 中间件验证 agent `low/medium` 保留、任务显式强度优先、Main/`high` 回退、模型与其他参数保留及输入不变。该回归检查在修复前失败；本次重跑包检查与 Core 启动检查。下列 Runtime 协议检查沿用 0.3.0 结果。

- 独立新仓库，仅包含插件代码、声明、页面、文档和合成测试；未导入实验仓库 Git 历史。
- 官方 `rust-v0.153.4` 发布的 npm arm64 包：可执行文件及 code-mode-host 的 SHA256 固定在插件 `app-pins.json`，不是作者桌面内置版的校验值。
- `tests/check_package.py`：仓库索引、包文件、首次 TLS 初始化与重复启动、缺损证书拒绝、npm CLI 路径解析、缺失/不匹配 Runtime 拒绝、代理参数、请求校验和本机管理接口边界。
- `tests/check_runtime.py`：真实官方 Runtime 对接本地合成 Responses 服务，工具调用交回 Deter、工具结果继续对话、Runtime 关闭。
- 同一测试的 `--json-output`、`--validation-feedback`、`--validation-feedback --invalid-again`：JSON 输出、单次参数纠正、重复无效参数拒绝。
- `tests/check_host.py`：使用本地 Core 源码的原生包预检、真实 Registrar 与模型管理器、首次启动、本地 HTTPS 模型目录、无通行证拒绝、空白登录存储检查及停止。数据和 HOME 均在临时目录。
- 原生 `PluginStore` 从 Git 仓库安装、选择插件子目录、包预检和精确 commit 锁定通过；不改动用户正在使用的安装。
- 发布内容做个人绝对路径、常见访问令牌、JWT、私钥文本模式扫描；未发现匹配。运行数据与凭据不纳入版本控制。

以上合成与启动测试不调用官方模型，不读取作者凭据，不消耗作者账户额度。

## 未验证

- 第二台真实电脑上的 GUI 仓库安装、浏览器登录及真实生成。
- Windows、Linux、Intel Mac；其他 Core 与 Runtime 版本。
- 当前目录中每个模型的实际账户权限、服务端行为及额度。

不要把本地测试通过等同于上述跨机或远端验收通过。

可选 Core 检查：设置 `DETERMINFLOW_CORE` 为自己合法取得的 Core 源码目录，`CODEX_TEST_RUNTIME` 为固定版本原生可执行文件路径，运行 `python tests/check_host.py`。仓库不包含 Core 源码。
