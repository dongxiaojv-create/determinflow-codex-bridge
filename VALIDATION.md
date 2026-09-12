# 0.3.8 发布验证

日期：2026-09-12。此版本为 macOS Apple Silicon 预览版。

## 已验证

- 0.3.8：本机复现用户 Codex 配置的 `service_tier=priority` 被子进程继承、导致账户及生成配置检查失败；现在子进程显式覆盖为 `default`，保留原有配置校验。真实 Runtime 流式检查预置该用户配置，验证覆盖生效且文件未变；无新增模型替换或用户配置写入。

- 0.3.7：普通正文经 Runtime 的文字增量实时交付；按消息 ID 和完整正文前缀校验，补齐缺失尾部并拒绝交错/不一致输出。JSON 和工具参数保持完整校验后交付。
- `tests/check_streaming.py`：真实本地 HTTP 连接用 gate 证明终态之前已收到正文；检查无重复、usage、流开始前/中途错误、断开和停用回收、静默期间空分块。可选真实 Core/OpenAI/LangChain 检查确认流中错误不自动重试，LangGraph 裸循环 break 会关闭 HTTP 连接。
- `tests/check_runtime_streaming.py`：固定官方 Runtime + 空 HOME + 本地合成服务，验证真实增量、多消息/补尾、内部推理隔离、JSON/Schema 成功及失败、冲突/交错拒绝；取消收到中断确认且进程退出。8 次本机合成请求、0 次官方模型请求。

- 0.3.6：真实桌面版冻结后端复现旧 `-m` 入口的 `ModuleNotFoundError`；改为绝对脚本入口后，空目录完成官方 Runtime 下载、双 SHA256 校验和 11 项模型目录准备（本机约 13 秒），未读取用户登录或发送生成请求。
- `tests/check_onboarding.py`：从 manifest 读取真实命令，通过不含插件 cwd 的隔离 Python 路径检查；`DETERMINFLOW_DESKTOP_PYTHON` 可直接指定冻结后端，本次亦通过该路径。
- `tests/check_host.py`：真实 Core 与独立 Executor 进程通过同步/异步客户端访问本地 HTTPS 模型目录；预置无效代理和证书环境时仍正常，插件 register/start/stop 前后环境字典不变，错误 CA 与无通行证请求被拒绝，停止后连接池关闭。
- TLS 信任与代理绕过均限于桥接器专用 HTTPX 客户端；不再设置 Deter 进程 `SSL_CERT_FILE`，不改系统网络或 Codex 用户配置。

- 0.3.5：无预装 CLI 的临时目录完成官方固定组件真实下载、双 SHA256 校验、执行与未登录模型目录初始化；没有读取作者账户或调用模型。
- `tests/check_onboarding.py`：原子安装、重复安装复用、压缩包符号链接拒绝、校验失败不安装、官方 URL 限制、已有登录跳过、模拟登录成功/失败/取消及进程回收。设置 `CODEX_TEST_RUNTIME` 后再检查真实 Runtime 的未登录目录准备。
- `tests/check_host.py`：真实 Core lifecycle 子进程准备目录后启动，首次启动得到完整模型目录且无需第二次重启；测试明确抑制交互登录。
- `tests/check_usage_ui.cjs`：新增登录等待/链接、取消清理、恶意链接拒绝和登录成功状态的真实浏览器检查。
- 真实用户的官方浏览器授权回调尚未验收；上述模拟登录通过不能替代此项。


- 0.3.4：故障返回阶段、请求编号和有证据支持的结果状态；已尝试提交但没有终态时保留 unknown，不触发桥接器重试。错误响应保留 HTTP 400，避免 Core/OpenAI 客户端自动重试。原始异常内容不进入用户错误消息。
- 新增 `tests/check_diagnostics.py`：执行生产生成路径和 HTTP 错误处理，覆盖启动、认证、提交结果未知、Runtime 失败、生成后 JSON 校验失败和取消，检查进程关闭与记录落盘。
- 新增作者仓库 GitHub Actions：每次 push/PR 对最终提交运行包、参数选择、用量、诊断、浏览器及固定 Runtime 的本地合成测试。不读取账户凭据、不调用官方模型。

- 0.3.3 修复 0.3.2 生成路径中 `account/read` 多传一个位置参数的回归。最终代码重跑真实 Runtime + 本地合成服务的 JSON 生成/工具交回测试及包检查；增加所有异步 RPC 调用的签名检查。未重试用户章节任务。

- `tests/check_usage_ui.cjs`：真实 Chromium 页面检查零余额、未知额度、部分记录覆盖、文本转义及失败后清除旧值；可设置 `CHROME_PATH` 使用已安装浏览器。
- 本机集成版已通过实际登录账户的只读额度查询，未发送模型生成请求；开源版仍未完成第二台电脑验收。
- 0.3.2：新增账户额度读取与本机 token 汇总；`tests/check_usage.py` 覆盖新旧记录、流式缺失回退、重复计数、未知/零区分、部分覆盖与多额度窗口。包检查验证管理接口边界和生成期间的本机统计；Core 检查验证未登录时额度为未知。

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
