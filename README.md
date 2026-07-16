# Subscription Triad

让 **Codex 负责调研和规划，Claude Fable 5 负责独立审核，Grok Build 负责执行，最后由 Codex 验收**。所有模型都走各自官方 CLI 的订阅登录，不需要 API Key。

> Status: early preview (`0.3.0`). The approval gate, one-authorization feature session, and local state machine are implemented and tested; provider availability still depends on the installed official CLIs and current subscription terms.

```mermaid
flowchart LR
    U["用户需求"] --> C["Codex root\n调研 + 规划"]
    C --> H["计划版本 + SHA-256"]
    H --> S["一次功能一次授权\n临时 provider 会话"]
    S --> F["Claude Fable 5\n只读审核"]
    F -->|"PLAN_REVISE"| C
    F -->|"PLAN_APPROVED"| G["Grok Build OAuth\n执行批准计划"]
    G --> A["agmsg / 内置传输\n本地状态消息"]
    A --> V["Codex root\n检查 diff + 测试 + 验收"]
    V -->|"范围内修正"| G
    V -->|"通过"| D["完成"]
```

## 为什么做这个项目

[Codex-Orchestration](https://github.com/Cjbuilds/Codex-Orchestration) 已经证明了“Planner / Advisor / Executor + 最终由 Codex 收口”的门禁模式；[agmsg](https://github.com/fujibee/agmsg) 提供了跨 CLI agent 的本地持久消息层。Subscription Triad 将两者的关键优势收束到用户的特定工作流中：

- Codex 当前任务始终是根协调者，不把最终决策交给其他模型。
- Fable 只能审核计划：无工具、无编辑、无会话持久化。
- 审批绑定计划 SHA-256；计划一改，原审批自动失效。
- Grok 只在明确批准后启动，并复用同一个 feature session 处理范围内修正。
- 已安装 agmsg 时只通过公开的 `join.sh`、`send.sh`、`api.sh` 接口使用；未安装时自动切换到项目内的 Python 标准库传输，不要求用户额外安装或配置。
- Grok 完成不等于项目完成；Codex 必须独立检查代码和测试。

## 订阅制安全边界

| 模型 | 实际调用 | 认证约束 |
|---|---|---|
| Codex | 当前 Codex 任务 | 使用用户已有的 ChatGPT/Codex 登录 |
| Fable | 官方 `claude -p --model claude-fable-5` | 必须报告 Claude.ai first-party Pro/Max |
| Grok | 官方 `grok --oauth --model grok-4.5` | 强制 OAuth 路径，不构造 xAI API 请求 |

每个 Claude/Grok 子进程都会移除 `ANTHROPIC_API_KEY`、`XAI_API_KEY` 和相关自定义端点/云平台变量。Grok 子进程还强制设置 `GROK_DISABLE_API_KEY_AUTH=1`，通过 `grok inspect --json` 验证 CLI 已禁用 API-key 认证，并显式使用 `--cwd <目标仓库> --sandbox workspace`：代码写入受 Grok 的 OS 级沙箱限制在目标仓库，另外只保留 Grok 自身状态目录和系统临时目录所需的写权限。项目不会读取、保存或转发 OAuth Token。

Grok Build CLI 目前没有提供与 `claude auth status` 同等强度的机器可读“订阅类型”证明，因此这里采用可验证的最小边界：禁用 API-key 认证、强制官方 CLI 的 `--oauth`、清除端点覆盖、检查当前 Grok Build 模型可用（优先 `grok-4.5`，兼容旧 `grok-build` 别名），并且从不调用 `api.x.ai`。这不是对厂商条款永不变化的保证。

## 前置条件

- Codex CLI/Desktop，以及有效的 ChatGPT/Codex 订阅登录。
- 官方 Claude Code CLI，以及 Claude Pro/Max 登录。
- 官方 Grok Build CLI，以及 SuperGrok/X Premium Plus 对应的 OAuth 登录。
- Python 3.9+。

认证：

```bash
claude auth login
grok login --oauth
```

不要设置 Anthropic 或 xAI API Key。

agmsg 不再是安装前置条件：如果系统已经安装 [agmsg](https://github.com/fujibee/agmsg)，插件会复用其公开脚本；否则自动使用内置的本地生命周期传输。两种模式都不需要 daemon，也不会发送网络消息。

### 零配置网络边界

Subscription Triad **不会写入**目标仓库的 `.codex/config.toml`，也不会修改用户的 `~/.codex/config.toml`、默认沙箱或默认网络权限。

插件内 MCP 只负责本地状态、计划哈希和生成受限的 provider 会话输入。Codex 完成调研、创建 run 并记录计划后，只针对插件内这一条命令请求一次宿主授权：

```text
python3 triad_provider.py session --run <本次功能的 run_dir>
```

这个临时进程被锁定到一个 `run_id + 目标仓库`，只接受换行分隔的五种 JSON 指令：

- `doctor`：检查官方 CLI、认证和模型可用性，不调用模型；
- `review`：仅把当前哈希绑定的审核包交给 Claude Fable；
- `dispatch` / `continue`：仅在计划已批准后调用官方 Grok OAuth CLI；
- `close`：完成后主动关闭会话。

后续指令通过已经批准进程的 stdin 发送，不再启动新的联网命令，因此一次正常功能只需要一次宿主授权。进程不接受 shell 文本或其他目录；私有租约每 3 秒续期，空闲 30 分钟或总计 4 小时自动退出。如果桥接进程消失，租约失效会终止仍在运行的 Grok worker。

这意味着安装后没有针对每个项目的配置步骤。每个新功能首次启动临时会话时仍可能出现 Codex 的标准授权提示；同一会话内的 Fable 复审、Grok 执行和范围内返工不再重复询问。新功能、会话超时/关闭、Codex 重启或会话句柄丢失时需要重新授权。插件尊重用户已有的“Ask for approval / Approve for me”选择，但不会改动它，也不会创建持久规则、后台 daemon 或默认网络权限。

## 安装 Codex 插件

普通用户不需要先克隆仓库，执行两条命令即可：

```bash
codex plugin marketplace add Mnufs/subscription-triad
codex plugin add subscription-triad@subscription-triad
```

然后新建一个 Codex 任务，让新 Skill 和 MCP 工具进入上下文。插件不会替用户安装或登录厂商 CLI；这是仅有的账号侧准备。

需要开发本插件时，才使用本地 marketplace：

```bash
git clone https://github.com/Mnufs/subscription-triad.git
cd subscription-triad
codex plugin marketplace add "$(pwd)"
codex plugin add subscription-triad@subscription-triad
```

## 使用

在目标项目的 Codex 任务中说：

```text
Use $subscription-triad to implement this feature:
<你的功能需求>
```

典型运行阶段：

1. `create_run`：把需求、验收条件和真实仓库事实写入本地运行目录。
2. `record_plan`：Codex 保存计划版本及 SHA-256。
3. `start_provider_session`：针对本次功能启动唯一一个临时进程，并请求一次宿主授权。
4. `doctor`：通过该进程验证 CLI、订阅认证和本地传输，不调用模型。
5. `review_plan`：通过同一进程让 Fable 返回 `PLAN_APPROVED` 或 `PLAN_REVISE`，最多五轮。
6. `dispatch_grok`：通过同一进程后台执行批准计划。
7. `run_status`：读取状态、产物和 agmsg 消息。
8. `continue_grok`：通过同一进程，用一次性、哈希绑定的修正包复用原 Grok session。
9. `record_verification`：Codex 记录独立验收；只有 `pass` 才完成。
10. `close_provider_session`：主动关闭临时进程并删除私有租约。

运行产物默认保存在：

```text
<project>/.subscription-triad/runs/<uuid>/
```

其中可能包含需求、仓库上下文、计划、Fable 审核、Grok 输出和验收报告。建议目标项目将 `.subscription-triad/` 加入 `.gitignore`。

## 手动 CLI

插件内同时提供纯标准库 CLI，便于开发调试：

```bash
TRIAD="plugins/subscription-triad/skills/subscription-triad/scripts/triad_cli.py"

python3 "$TRIAD" doctor --project /path/to/project
python3 "$TRIAD" create \
  --project /path/to/project \
  --task-file task.md \
  --acceptance-file acceptance.md \
  --context-file context.md
```

其他子命令可通过 `python3 "$TRIAD" --help` 查看。

`triad_provider.py` 是 Codex 使用的受限宿主桥，命令行只开放 `session --run <run_dir>`；会话内部只接受 `doctor`、`review`、`dispatch`、`continue`、`close` 五种 JSON 动作。它不是通用命令执行器。

## 缓存策略与取舍

“缓存命中”通常依赖供应商对稳定提示词前缀的匹配，订阅产品不一定公开命中率或精确 Token。项目只优化可控制的部分：

- Codex 在同一根任务里完成调研、规划和验收。
- Fable 使用稳定 system prompt，但每轮保持无状态，优先保证审核独立性。
- 每个功能只创建一个 Grok session；验证修正通过 `--resume` 继续。
- 大上下文落本地 artifact，agmsg 只传短状态和路径，避免重复发送完整对话。
- Grok 禁用跨 session memory，防止不同项目互相污染；同一 session 内的上下文仍保留。

因此这个项目明确选择：**Fable 的独立审核优先于会话复用；Grok 的连续执行优先复用同一会话。**

## 本地开发

```bash
python3 -m unittest discover -s tests -v
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" \
  plugins/subscription-triad/skills/subscription-triad
```

第二条命令中的 validator 路径只适用于本机 Codex 开发环境；CI 会执行仓库自带的结构和行为测试。

## 上游与许可证

本项目基于 MIT 许可下的架构思想和部分安全调用模式构建：

- [Cjbuilds/Codex-Orchestration](https://github.com/Cjbuilds/Codex-Orchestration)
- [fujibee/agmsg](https://github.com/fujibee/agmsg)

详细归属见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。Subscription Triad 本身采用 MIT License。

## English summary

Subscription Triad is a zero-project-config Codex plugin for a fail-closed, subscription-only workflow: Codex plans, Claude Fable 5 reviews the exact plan, Grok Build executes after approval, agmsg or an embedded local transport carries lifecycle messages, and Codex independently verifies the result. One temporary, run-bound provider process covers the feature's readiness check, reviews, execution, and bounded continuations under a single host authorization. It expires automatically and never changes persistent Codex network settings. No API key is required or accepted by provider subprocesses.
