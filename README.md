# DeterminFlow Codex Bridge

在 DeterminFlow 中使用**你自己登录的官方 Codex CLI 账户**。通过插件仓库安装，不需要 Codex 桌面应用，不提供共享账户或共享额度。

**0.3.2 预览版：仅 macOS Apple Silicon。** 基于 DeterminFlow Desktop 1.1.0 / Core `9db9d98c` 的扩展接口，固定官方 Codex CLI `0.153.4`。其他 Core、CLI 版本、Intel Mac、Windows、Linux 尚未验收。此项目不是 OpenAI 或 DeterminFlow 官方插件。

工作流推理强度优先级：任务显式覆盖 → agent 自身设置 → Main 默认 → `high`。0.3.1 修复了 Main 强度覆盖 agent 设置的问题；已创建任务的冻结配置不追溯修改。

## 安装

1. 安装 [Node.js](https://nodejs.org/)，然后在终端运行：

   ```sh
   npm install -g @openai/codex@0.153.4
   codex login
   codex login status
   ```

   在官方浏览器页面登录**自己的** ChatGPT 账户。仅 API Key 登录不适用于本插件当前版本。已有 CLI 时不要直接降级正在使用的环境；可单独安装固定版本并在插件设置指定其路径。

2. 在 DeterminFlow 的「插件 → 添加仓库」填入：

   - 名称：`Codex Bridge`
   - Git 仓库地址：`https://github.com/dongxiaojv-create/determinflow-codex-bridge.git`
   - 分支或标签：`main`

3. 保存并拉取，选择 **Codex Bridge**，确认第三方插件权限后安装、启用，重启 DeterminFlow。
4. 在插件设置中按需填写：

   - `codex_path`：CLI 完整路径；留空检查常见目录。若使用 nvm 或自定义安装，填写终端 `command -v codex` 的结果。
   - `proxy`：Codex Runtime 的 HTTP(S) 代理，例如 `http://127.0.0.1:7890`；留空直连。插件不修改系统代理或其他供应商的代理。

   保存设置后重启 DeterminFlow。
5. 打开插件「Codex 登录状态」页面，检查账户并刷新模型目录；按页面提示重启，再在原模型菜单选择 **Codex Bridge**、模型与推理强度。

首次启动只在插件数据目录生成本地 TLS 证书。无需运行旧版 `install_app.py`，不会下载 Runtime、复制账户凭据或上传本机数据到本仓库。

## 账户与额度

调用链：`DeterminFlow → 本机 Bridge → 官方 Codex app-server → OpenAI`。

Bridge 使用当前系统用户的 `CODEX_HOME`（默认 `~/.codex`）；凭据由官方 Runtime 从该目录或系统凭据库加载。账户检查页面只显示账户类型和邮箱，不显示令牌。登录、退出均通过官方 CLI；退出后，依赖同一登录存储的其他 Codex 客户端也可能需要重新登录。

**登录谁，就使用谁的账户额度。** 克隆仓库不会取得作者账户。不要提交 `auth.json`、`.codex`、API Key、插件数据目录、私钥或运行记录。也不要把已登录的本机服务通过端口转发提供给他人。

本插件仅提供回环接口，并给模型接口配置每次启动随机生成的通行证。插件与 DeterminFlow 共享系统权限，不是同机恶意软件的安全隔离层。请求内容会发送到 OpenAI；本地插件数据还会保存模型结果、工具参数、用量和失败状态，分享故障资料前需脱敏。

官方资料：[Codex 认证](https://developers.openai.com/codex/auth/) · [固定版本发布](https://github.com/openai/codex/releases/tag/rust-v0.153.4)

## 能力与限制

- 普通聊天、工作流、多轮文字历史；模型工具调用交回 DeterminFlow 执行。
- 每次请求使用独立 Runtime，会话不共享；后续请求由 DeterminFlow 提供完整历史。
- 保留指定模型，不自动替换；模型目录可见不等于账户已获该模型访问权。
- JSON 对象/Schema 和严格工具参数有本地校验；不能宣称等价于原生严格约束解码。
- Runtime 未提供 temperature/top_p/penalty 的语义映射，插件不会伪装为支持。
- 当前是完整结果返回后包装为 SSE，**不是真正逐 token 流式输出**。长推理需在模型参数设置足够的流式分块超时。
- 请求失败或断开后，远端是否完成及用量可能未知；不会把未知当零用量。

## 开发检查

Python 3.11+；检查依赖为 `httpx fastapi uvicorn cryptography certifi jsonschema`，运行时由兼容的 DeterminFlow 环境提供。

```sh
python tests/check_package.py
python tests/check_selection.py
python tests/check_usage.py
CODEX_TEST_RUNTIME=/absolute/path/to/native/codex python tests/check_runtime.py
CODEX_TEST_RUNTIME=/absolute/path/to/native/codex python tests/check_runtime.py --json-output
CODEX_TEST_RUNTIME=/absolute/path/to/native/codex python tests/check_runtime.py --validation-feedback
CODEX_TEST_RUNTIME=/absolute/path/to/native/codex python tests/check_runtime.py --validation-feedback --invalid-again
```

Runtime 检查使用临时 HOME 和本地合成服务，不读取个人登录、不调用官方模型。发布验证范围见 [VALIDATION.md](VALIDATION.md)；第二台真实电脑安装和真实账户调用尚待验证。

## 许可

插件代码采用 [MIT](LICENSE)。官方 Codex CLI 和 DeterminFlow 使用各自的许可；本仓库不分发其二进制或源码。

### 账户与用量

在 DeterminFlow 的插件列表打开「Codex 账户与用量」。页面自动读取一次，也可手动刷新：

- 账户：通过官方 `account/rateLimits/read` 读取实际 CLI 登录账户的额度窗口、重置时间与 credits 余额；这与该账户其他设备共享，不是人民币或本插件独享余额。不可用时显示未知。
- 本机：按模型汇总保留调用记录中的输入、输出、缓存、推理 token，显示有记录/总调用次数及缺失数量。它可能包含历史登录账户的调用；只汇总 Runtime 返回的记录，不作为官方完整账单。缓存和推理为子集，不重复加进总量。
- 刷新不发送模型生成请求。生成期间仍可看本机统计，账户查询需等当前节点结束。

可选页面检查（需已有 Playwright）：`node tests/check_usage_ui.cjs`；可用 `CHROME_PATH` 指定浏览器可执行文件。
