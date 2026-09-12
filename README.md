# DeterminFlow Codex Bridge

在 DeterminFlow 中使用**你自己登录的官方 Codex CLI 账户**。通过插件仓库安装，不需要 Codex 桌面应用，不提供共享账户或共享额度。

**0.3.13 预览版：仅 macOS Apple Silicon。** 基于 DeterminFlow Desktop 1.1.0 / Core `9db9d98c` 的扩展接口，固定官方 Codex CLI `0.153.4`。其他 Core、CLI 版本、Intel Mac、Windows、Linux 尚未验收。此项目不是 OpenAI 或 DeterminFlow 官方插件。

工作流推理强度优先级：任务显式覆盖 → agent 自身设置 → Main 默认 → `high`。0.3.1 修复了 Main 强度覆盖 agent 设置的问题；已创建任务的冻结配置不追溯修改。

## 安装

1. 在 DeterminFlow「插件 → 添加仓库」填入：

   - 名称：`DeterminFlow Codex Bridge`
   - Git 仓库地址：`https://github.com/dongxiaojv-create/determinflow-codex-bridge.git`
   - 分支或标签：`main`

2. 保存并拉取，选择 **Codex Bridge**，确认第三方权限后安装、启用，按宿主提示重启一次。
3. 首次启用时，插件自动从官方 npm 源下载固定版本运行组件并验证 SHA256，然后准备模型目录。无需安装 Node.js、Codex CLI 或 Codex 桌面应用。下载失败时可在插件设置填写 HTTP(S) `proxy` 后重启重试。
4. 未登录时自动打开官方浏览器登录页。完成自己的 ChatGPT 账户授权后，回到 DeterminFlow，选择 **Codex Bridge** 模型即可使用，无需手动刷新模型目录或再次重启。
5. 浏览器未打开、取消或登录超时，可打开插件「Codex 账户与用量」页，点击 **登录 / 更换 ChatGPT 账户**；等待期间会显示官方登录链接。

已有当前系统用户的 ChatGPT 登录会直接复用，不重复弹出登录页。首次取消后不会在每次启动时强制弹窗。登录和目录读取不发送模型生成请求；模型可见不保证该账户拥有访问权限或剩余额度。

`codex_path` 留空使用插件管理的运行组件；高级用户可指定同一固定版本的完整路径。运行组件只写入插件数据目录，不改系统 PATH 或升级用户已有 CLI。当前仍受宿主限制，需要首次启用时重启一次；不是安装操作完成瞬间热加载插件。

## 账户与额度

调用链：`DeterminFlow → 本机 Bridge → 官方 Codex app-server → OpenAI`。

Bridge 使用当前系统用户的 `CODEX_HOME`（默认 `~/.codex`）；凭据由官方 Runtime 从该目录或系统凭据库加载。账户检查页面只显示账户类型和邮箱，不显示令牌。登录由插件启动固定官方 CLI 的浏览器授权流程，凭据仍由官方 CLI 保存。更换登录账户会影响共用该登录存储的其他 Codex 客户端。

**登录谁，就使用谁的账户额度。** 克隆仓库不会取得作者账户。不要提交 `auth.json`、`.codex`、API Key、插件数据目录、私钥或运行记录。也不要把已登录的本机服务通过端口转发提供给他人。

本插件仅提供回环接口，并给模型接口配置每次启动随机生成的通行证。插件与 DeterminFlow 共享系统权限，不是同机恶意软件的安全隔离层。请求内容会发送到 OpenAI；本地插件数据还会保存模型结果、工具参数、用量和失败状态，分享故障资料前需脱敏。

本地 HTTPS 的证书信任只交给桥接器专用客户端；不改写 DeterminFlow 进程的代理或 `SSL_CERT_FILE`，不修改系统代理、系统证书库或 Codex 用户配置。

官方资料：[Codex 认证](https://developers.openai.com/codex/auth/) · [固定版本发布](https://github.com/openai/codex/releases/tag/rust-v0.153.4)

## 能力与限制

- 普通聊天、工作流、多轮文字历史；模型工具调用交回 DeterminFlow 执行。
- 每次请求使用独立 Runtime，会话不共享；后续请求由 DeterminFlow 提供完整历史。
- 关闭桥接器不使用的 Runtime 自带多代理工具和说明，减少固定输入开销；Deter/Bishu 的 agent、业务工具和完整历史保持原样。
- 保留指定模型，不自动替换；模型目录可见不等于账户已获该模型访问权。
- 桥接器子进程明确使用 `default` 服务档位，避免继承 Codex 客户端的 `priority` 设置后无法通过配置检查；不改写用户 Codex 配置。
- JSON 对象/Schema 和严格工具参数有本地校验；不能宣称等价于原生严格约束解码。
- Runtime 未提供 temperature/top_p/penalty 的语义映射，插件不会伪装为支持。
- 普通文字实时逐段返回；JSON 对象/Schema 在完整校验后交付，工具参数在校验和 Runtime 中断确认后交回。不会把内部推理内容当正文输出。
- 生成开始后每秒发送空分块，让宿主在静默推理和 JSON 等待期间也能处理停止；断开连接或停用时中断并回收 Runtime。开始流式前的失败返回 HTTP 400，流中失败明确返回错误，不伪造成功结尾或主动重试。
- 请求失败或断开后，远端是否完成及用量可能未知；不会把未知当零用量。

## 开发检查

Python 3.11+；检查依赖为 `httpx fastapi uvicorn cryptography certifi jsonschema`，运行时由兼容的 DeterminFlow 环境提供。

```sh
python tests/check_package.py
python tests/check_selection.py
python tests/check_usage.py
python tests/check_diagnostics.py
python tests/check_platform.py
python tests/check_streaming.py
python tests/check_onboarding.py
CODEX_TEST_RUNTIME=/absolute/path/to/native/codex python tests/check_runtime.py
CODEX_TEST_RUNTIME=/absolute/path/to/native/codex python tests/check_runtime_streaming.py
CODEX_TEST_RUNTIME=/absolute/path/to/native/codex python tests/check_runtime.py --json-output
CODEX_TEST_RUNTIME=/absolute/path/to/native/codex python tests/check_runtime.py --validation-feedback
CODEX_TEST_RUNTIME=/absolute/path/to/native/codex python tests/check_runtime.py --validation-feedback --invalid-again
```

Runtime 检查使用临时 HOME 和本地合成服务，不读取个人登录、不调用官方模型。发布验证范围见 [VALIDATION.md](VALIDATION.md)；第二台真实电脑安装和真实账户调用尚待验证。

可设置 `DETERMINFLOW_CORE_ROOT` 为本地 Core 源码目录，再运行 `tests/check_streaming.py`，验证真实 SDK/Core 对流中错误的重试抑制、空分块和停止时关闭连接。

`tests/check_onboarding.py` 在设置 `CODEX_TEST_RUNTIME` 后还检查桌面生命周期脚本入口；可设置 `DETERMINFLOW_DESKTOP_PYTHON` 为实际桌面版内置后端，直接验证冻结运行环境。

## 许可

插件代码采用 [MIT](LICENSE)。官方 Codex CLI 和 DeterminFlow 使用各自的许可；本仓库不分发其二进制或源码。

### 账户与用量

在 DeterminFlow 的插件列表打开「Codex 账户与用量」。页面自动读取一次，也可手动刷新：

- 账户：通过官方 `account/rateLimits/read` 读取实际 CLI 登录账户的额度窗口、重置时间与 credits 余额；这与该账户其他设备共享，不是人民币或本插件独享余额。不可用时显示未知。
- 本机：按模型汇总保留调用记录中的输入、输出、缓存、推理 token，显示有记录/总调用次数及缺失数量。它可能包含历史登录账户的调用；只汇总 Runtime 返回的记录，不作为官方完整账单。缓存和推理为子集，不重复加进总量。
- 近期调用：展示最近 50 次记录的时间、模型、推理强度、插件版本、耗时、状态及用量。没有 agent/章节关联标识的请求不会猜测归属；历史记录缺失的字段显示未知。
- 0.3.10 起使用每个独立 Runtime 线程报告的累计用量，计入工具参数纠正带来的再次生成，并收集工具交回或取消收尾时到达的用量通知。旧记录可能只记了最后一次生成，保留原值并标注；失败/中断时的已报告用量仍可能不完整。
- 刷新不发送模型生成请求。生成期间仍可看本机统计，账户查询需等当前节点结束。

可选页面检查（需已有 Playwright）：`node tests/check_usage_ui.cjs`；可用 `CHROME_PATH` 指定浏览器可执行文件。

### 故障定位与发布检查

失败消息包含阶段和请求编号。本机插件数据目录的 `attempts/<编号>.json` 保存相同诊断。`not_submitted` 表示尚未提交生成；`unknown` 表示尝试提交后无法确认结果，请先核查而不要直接重复提交。`runtime_failed` / `runtime_interrupted` 是 Runtime 报告的终态，不保证没有消耗额度；`completed` 表示生成结束但结果交付或校验失败。诊断不展示原始异常、提示词或凭据。

[自动检查](https://github.com/dongxiaojv-create/determinflow-codex-bridge/actions/workflows/check.yml) 在每次推送和 PR 上运行固定 Runtime 的合成生成、JSON、工具交回以及离线错误/取消测试。发布前检查对应提交全部通过；绿色检查不替代第二台电脑和真实账户验收。

运行组件下载源为 `registry.npmjs.org/@openai/codex` 的固定 0.153.4 Apple Silicon 包。插件不把二进制或登录凭据提交到仓库；官方组件的许可证见 [Codex 源码许可证](https://github.com/openai/codex/blob/rust-v0.153.4/LICENSE)。插件数据目录增加固定版本运行组件、安装锁、模型目录和一次性登录提示标记；卸载时由宿主的数据保留/删除策略处理。登录使用的用户 Codex 凭据不会随运行组件一起删除。
