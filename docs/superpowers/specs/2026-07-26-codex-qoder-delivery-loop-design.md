# Codex–Qoder 可审计交付循环设计

## 目标

在任意 Git 项目中，把一个明确的新需求或缺陷修复从需求描述自动推进到可审查的交付物。Codex 是唯一入口和出口：负责需求澄清、设计、验收契约、调用 Qoder、审查与最终报告；Qoder CLI 负责计划、TDD 实现、执行测试与提交。流程可同时运行多个互不影响的任务，并保留可调查证据。

本设计将固化为个人 skill：`codex-qoder-delivery-loop`。

## 不可变约束

- 每个任务只在自己的 linked Git worktree 中修改文件；不得在主工作区执行实现代理。
- 任务初始基线、输入提示词、计划、每轮 Qoder 输出、测试输出、Git SHA、Codex 审查和最终结论必须落盘。
- Qoder 每个实现回合只能产生一个线性提交；任何未提交改动、改写历史或越界文件都会停止该任务。
- 所有执行命令必须有超时，终态必须显式重置，不能因一次暂态错误而静默重跑已完成工作。
- 并行任务不得共享 worktree、状态文件、日志或 Qoder session ID。
- 不把调用次数、字符数或估算价格伪装为真实 token/credit 消耗。

这是一套可信本地代理的流程控制方案，不是安全沙箱。Qoder 与 Codex CLI 仍以当前用户权限运行；外部命令、凭据和远程 Git 权限应由运行环境控制。

## 目录与任务契约

针对仓库根目录 `<repo>` 和任务 ID `<id>`：

```text
<repo>/.codex-qoder-loop/tasks/<id>/
  manifest.json             # 不可变输入、路径白名单、命令、模型、超时
  state.json                # 状态机、基线、轮次、session ID、错误
  design.md                 # Codex 设计
  acceptance.json           # 机器可检查的验收契约
  qoder-plan.md             # Qoder 的实施计划
  prompts/                  # 发给各 CLI 的完整提示词
  logs/                     # 原始 stdout/stderr 与 JSONL 事件
  reviews/                  # 每轮 Codex 审查结果
  usage/                    # 原始计量证据与汇总
  final-report.md           # Codex 最终交付报告
<repo>/.worktrees/codex-qoder-<id>/
```

`manifest.json` 至少包含任务文本哈希、允许路径、测试命令、最大回合数、每类命令超时、并发标签和可选的可信 usage 采集命令。变更 manifest 或设计/验收文件会使任务变为 `configuration_changed`，需要人工复核后显式重置。

## 状态机

```text
created → designed → planned → implementing → testing → reviewing
                                                    ├→ blocked → implementing
                                                    ├→ approved → delivered
                                                    └→ manual_review_required

任一阶段可进入：failed、timed_out、scope_violation、usage_unavailable
```

`blocked` 是唯一可自动回到 `implementing` 的状态，且不超过 `max_iterations`。`approved`、`delivered`、失败、超时、范围违规与人工审查均为终态；下一次执行必须传入明确的 reset/retry 标志并新建审计事件。

## 职责与协议

### Codex

1. 建立任务目录和 worktree，冻结基线 commit。
2. 把用户需求拆为 `design.md` 与可验证的 `acceptance.json`，指定允许目录和测试命令。
3. 调用 Qoder 的计划阶段，保存其计划；若与契约冲突则要求修订或停止。
4. 调用 Qoder 的实现阶段和测试阶段；每次调用均带上任务契约、前轮审查意见和“单次提交”约束。
5. 验证 Git 历史、改动范围、测试退出码，并用 `codex review --base <baseline>` 审查全部累计改动。
6. 在通过时写入最终报告；在不可自动判断的情形写入人工复核原因，而不声称已交付。

### Qoder CLI

1. 依据 Codex 提供的契约写入 `qoder-plan.md`，不改产品代码。
2. 依据已接受的计划，先写失败测试，再实现、运行指定测试，并创建一个提交。
3. 对 `BLOCKED` 审查意见只修复该意见涉及的范围，重新测试并再创建一个提交。
4. 输出结构化摘要（修改、测试、提交、阻塞项）；原始输出始终保留，即使无法解析结构化格式。

## 并行执行

启动器为每个任务分配 UUID/可读 ID、独立 worktree 和独立 Qoder session ID。任务清单中可设置 `parallelism`（默认 2）；调度器只并行运行不同任务 ID 的 Qoder 子进程，Git 创建 worktree 和最终汇总在短临界区内串行化。任务目录中的 `runner.lock` 防止同一任务被重复启动。

失败或进程退出后，恢复命令只读取该任务的 `state.json`、日志与 worktree HEAD，从最后一个完整阶段恢复；不扫描或修改其它任务。

## 用量计量

每次 Codex/Qoder CLI 调用创建一个 `usage/raw-<sequence>.*` 证据文件。采集器按以下优先级写入 `usage/summary.json`：

1. CLI JSON/JSONL 事件中明确提供的 input/output/total token、credit 或 cost 字段；
2. 对 Qoder，调用前后执行内置 `/usage`，保存总 Credits 余额与使用明细快照，并以两次余额差值推算该次 Qoder 调用的 Credits 消耗；
3. manifest 指定的可信 provider usage 命令/API 的调用前后快照差值；
4. `unavailable`。

Qoder CLI 不会在每次模型调用结束时直接输出本次消耗。`/usage` 是账户级余额；多个 Qoder 调用并行时，前后差值不能可靠归属给某一调用。本 skill 不使用账户级计量锁：只要任务允许并行 Qoder 调用，即保存 `/usage` 快照但将该调用的 Credits 标记为 `unavailable`。只有 manifest 明确声明该调用在账户上独占执行时，才可使用前后快照差值。汇总按 `codex` 与 `qoder` 分组，并记录调用次数、模型、耗时、证据路径与计量方法。若 manifest 启用 `require_usage=true`，`unavailable` 会阻止 `delivered`，转为 `usage_unavailable`。

## Skill 内容与验证

skill 目录包含：

- `SKILL.md`：启动条件、角色边界、工作流、停止规则；
- `scripts/init_task.py`：创建 task manifest、审计目录和 worktree；
- `scripts/run_task.py`：执行状态机、调用 Qoder、测试与 Codex 审查；
- `scripts/collect_usage.py`：保存原始计量证据并生成汇总；
- `scripts/inspect_task.py`：输出可读的任务调查报告；
- `references/manifest-schema.md`：任务配置字段和状态定义；
- `agents/openai.yaml`：可发现的 UI 元数据。

测试覆盖：任务 ID 隔离、范围违规、零/多提交、终态重跑、超时、恢复、并发锁、usage 可用/不可用以及最终报告内容。以临时 Git 仓库和替身 CLI 运行测试；不得对真实 Qoder 或 Codex 账户产生模型调用。

## 验收标准

- 输入一个限定范围的特性或缺陷描述后，能生成设计、验收契约、Qoder 计划、实现提交、测试记录、Codex 审查和最终报告。
- 两个任务可并行执行，且其 worktree、日志、状态和 session 不交叉。
- 每次停止都能从任务目录确定当前阶段、最后一次命令、基线/HEAD、失败原因和下一步动作。
- 最终报告分别展示 Codex 与 Qoder 的 token/credit；并行 Qoder 调用附上 `/usage` 快照证据但显示 `unavailable`，没有可信来源时同样明确显示 `unavailable`。
- 所有范围、提交数、历史、测试和终态保护由脚本验证，而非只依赖提示词。
