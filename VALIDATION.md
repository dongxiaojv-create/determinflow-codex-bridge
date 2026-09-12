# 0.3.13 发布验证

日期：2026-09-12。此版本为 macOS Apple Silicon 预览版。

## 已验证

- 0.3.13：生成流中断且最后一条消息未结束时，优先保留 Runtime 的失败终态，避免被消息完整性校验覆盖成 ValueError。已确认的响应流中断显示固定中文说明，不透传原始异常、地址或凭据，不推断断点或扣额情况。
- `tests/check_diagnostics.py` 在生产 HTTP 路径复现“部分 JSON + failed terminal”，验证错误分类、无完整结果交付、无原始异常泄露及进程回收。不重试、不切换模型；此更新改善诊断，不宣称修复外部传输故障。

- 0.3.12：JSON 对象和 JSON Schema 请求只将最终回答交给结构校验，排除 Runtime 标记为 commentary 的进度消息。此前将进度文字与有效 JSON 拼接，会把有效结果误判为格式错误；无 phase 的兼容输出、普通文本流和 Deter 工具交接保留原有行为。
- 使用固定官方 Runtime 与本地合成服务复现“进度说明 + 有效最终 JSON”失败，并覆盖修复后的对象、Schema、无 phase 输出及工具结果续写；不调用官方模型。此缺陷与另一次记录为 Runtime 网络流中断的请求不能直接等同，不宣称修复外部网络故障。

- 0.3.11：修复流式回复首尾重复发送 assistant 角色。OpenAI SDK 汇总 JSON 模式回复时会拼接重复角色，只有工具调用而没有正文时触发 ChatMessage(content=None) 校验失败。流式开始后不再重复发送角色，独立 SSE 序列化仍保留一次角色。
- 使用与桌面相同的 OpenAI SDK 2.44.0 / langchain-openai 1.3.3 离线复现原报错；修复后 JSON 模式的纯工具调用、带文字工具调用、工具参数及 token 用量完整通过。HTTP 流式、错误与取消回收检查通过；未重跑用户章节、未调用官方模型。

- 0.3.10：在独立 Runtime 线程结束后读取最后一条匹配 thread/turn 的累计用量，替代仅记录最后一次生成。合成服务每次报告 13 token，工具纠参的两次生成应记录 26；历史注入后的新线程仍只记录其本次 13，不重复累计旧线程。只提供 last 时标注 last_only，无通知时为 unknown。
- `tests/check_streaming.py`：在现有取消回收检查中覆盖中断握手期间晚到的累计通知、重复通知不叠加、其他 thread/turn 过滤、last_only/unknown，以及成功/失败/取消的调用元数据；首次运行前取消且取消记录写入失败时，仍清理运行状态。固定 Runtime 四种工具模式与八个流式场景通过，全程使用临时 HOME 和本地合成服务。
- 近期调用明细复用现有 attempts 记录，仅返回白名单字段，最多展示 50 条；旧字段不猜测、不改写。版本、强度、结束时间及耗时从新调用起记录；旧版用量可能漏计纠参，页面明确标注。

- 0.3.9：通过 `[agents] enabled=false` 关闭固定 Runtime 默认注入的额外多代理工具和说明；原有 `features.multi_agent=false` 不足以关闭这部分。Deter/Bishu 继续负责 agent 与业务工具执行。
- 固定 Runtime + 空 HOME + 本地合成服务的同场景对比：去除随机 item ID 后，上行 input JSON 的字符数分别为无工具短请求 13,797 → 5,512、3 工具短请求 14,277 → 5,992、12 轮长历史及 3 工具 64,453 → 56,168；每次固定减少 8,285 字符。完整请求体减少 8,381 字节。这是序列化输入规模，不是官方 token 或额度节省比例；长上下文中的相对降幅更小。
- `tests/check_runtime.py` 四种模式逐请求检查无额外多代理工具和说明、原始 system/developer/user 消息各保留一次、模型与推理强度保持原值；原有工具交回与工具结果续写检查通过。临时重新启用 Runtime agents 的负控正确触发回归断言；无需真实账户或模型请求。

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
- Windows：当前版本不支持。`tests/check_platform.py` 在 macOS 上模拟 Windows AMD64/arm64 和缺少 fcntl：运行组件查找器/安装器拒绝平台，真实生命周期和插件模块因 fcntl 无法导入，TLS 文件锁同样不可用。下载包、二进制名称及 SHA256 仅为 Darwin arm64；POSIX 环境变量和目录持久化也需要移植。模拟检查的 unsupported_expected 表示如实验证了限制，不能当成 Windows 启动成功；没有运行 Windows 操作系统或官方 Windows Runtime。
- Linux、Intel Mac；其他 Core 与 Runtime 版本。
- 当前目录中每个模型的实际账户权限、服务端行为及额度。

不要把本地测试通过等同于上述跨机或远端验收通过。

可选 Core 检查：设置 `DETERMINFLOW_CORE` 为自己合法取得的 Core 源码目录，`CODEX_TEST_RUNTIME` 为固定版本原生可执行文件路径，运行 `python tests/check_host.py`。仓库不包含 Core 源码。
