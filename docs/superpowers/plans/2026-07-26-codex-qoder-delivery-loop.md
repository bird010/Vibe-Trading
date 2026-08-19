# Codex–Qoder 可审计交付循环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在个人 Codex skills 目录生成一个可并行、可审计、以 Codex 编排 Qoder 的通用开发交付 skill。

**Architecture:** skill 本身只含可移植 Python 标准库脚本和说明。每个任务有自己的 manifest、状态、日志、usage 证据和 linked worktree；运行器通过子进程调用 Qoder、测试命令与 Codex review，所有关键后置条件由 Git 和状态机验证。

**Tech Stack:** Python 3 标准库、Git、`qoderclicn`、`codex` CLI、pytest。

## Global Constraints

- 创建路径固定为 `C:\Users\LK\.codex\skills\codex-qoder-delivery-loop`。
- 脚本不得执行 push、merge、reset 或删除 worktree。
- 每个任务必须使用 linked worktree、独立状态目录和独立 session ID。
- Qoder 计量默认调用前后执行 `/usage`，保留原始快照并计算 Credits 余额差值。
- 没有可信 token/credit 字段或 usage 快照时，报告 `unavailable`，不得估算成本。
- 使用标准库；测试只能调用临时 Git 仓库和替身 CLI，禁止真实模型调用。

---

### Task 0: 建立无 skill 基线场景

**Files:**
- Create: `C:\Users\LK\.codex\skills\codex-qoder-delivery-loop-validation\baseline.md`

**Interfaces:**
- Produces: 记录无本 skill 时代理面对并行、审计与不可用计量要求的原始决策。

- [ ] **Step 1: 向新鲜代理发出基线场景**

场景必须同时施加三项压力：两个任务要求并行；Qoder CLI 没有逐调用的 Credits 输出；其中一个任务在测试后被审查阻塞。不给出本 skill 内容，也不提供预期答案。

- [ ] **Step 2: 记录实际遗漏或不确定项**

将代理原始回答写入 `baseline.md`，特别记录是否遗漏隔离 worktree、终态保护、原始 usage 证据，或把不可用计量误报为数值。

- [ ] **Step 3: 以这些观察约束 skill 内容**

`SKILL.md` 必须对观察到的遗漏采用明确的结构化交付契约，而不是叙事性建议。

### Task 1: 初始化 skill 包与共享任务模型

**Files:**
- Create: `C:\Users\LK\.codex\skills\codex-qoder-delivery-loop\SKILL.md`
- Create: `C:\Users\LK\.codex\skills\codex-qoder-delivery-loop\agents\openai.yaml`
- Create: `C:\Users\LK\.codex\skills\codex-qoder-delivery-loop\scripts\common.py`
- Create: `C:\Users\LK\.codex\skills\codex-qoder-delivery-loop\tests\test_common.py`

**Interfaces:**
- Produces: `TaskPaths`, `LoopError`, `load_manifest(path)`, `write_json(path, value)`, `append_event(paths, kind, payload)`。
- Consumes: Python `pathlib`, `json`, `hashlib` 和 `uuid`。

- [ ] **Step 1: 写失败测试，固定任务目录和 manifest 校验**

```python
def test_task_paths_are_isolated(tmp_path: Path) -> None:
    first = TaskPaths.for_task(tmp_path, "fix-api")
    second = TaskPaths.for_task(tmp_path, "fix-ui")
    assert first.root != second.root
    assert first.state == first.root / "state.json"

def test_load_manifest_rejects_parent_traversal(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"allowed_paths": ["../src"], "test_commands": ["pytest"]}')
    with pytest.raises(LoopError, match="allowed_paths"):
        load_manifest(path)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -B -m pytest tests/test_common.py -q`

Expected: FAIL，提示 `common` 模块或目标符号不存在。

- [ ] **Step 3: 实现最小共享模型与原子 JSON 写入**

```python
@dataclass(frozen=True)
class TaskPaths:
    root: Path
    state: Path
    manifest: Path
    events: Path
    worktree: Path

    @classmethod
    def for_task(cls, repo: Path, task_id: str) -> "TaskPaths":
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", task_id):
            raise LoopError("invalid task id")
        root = repo / ".codex-qoder-loop" / "tasks" / task_id
        return cls(root, root / "state.json", root / "manifest.json", root / "events.jsonl", repo / ".worktrees" / f"codex-qoder-{task_id}")

def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
```

要求 manifest 至少校验 `task`, `allowed_paths`, `test_commands`, `max_iterations`；路径必须相对且不含 `..`，测试命令必须非空。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -B -m pytest tests/test_common.py -q`

Expected: PASS。

- [ ] **Step 5: 记录验证证据**

将通过的命令、pytest 输出摘要与 skill 根目录写入 `validation/task-1.md`。个人 skills 目录不假设是 Git 仓库，因此不创建无意义的提交。

### Task 2: 建立任务、worktree 和可恢复锁

**Files:**
- Create: `C:\Users\LK\.codex\skills\codex-qoder-delivery-loop\scripts\init_task.py`
- Create: `C:\Users\LK\.codex\skills\codex-qoder-delivery-loop\tests\test_init_task.py`
- Modify: `C:\Users\LK\.codex\skills\codex-qoder-delivery-loop\scripts\common.py`

**Interfaces:**
- Consumes: `TaskPaths`, `write_json`, `append_event`。
- Produces: `create_task(repo, task_id, manifest) -> TaskPaths` 与 `acquire_lock(paths) -> None`。

- [ ] **Step 1: 写失败测试，验证 linked worktree 和重复启动锁**

```python
def test_create_task_creates_a_linked_worktree(tmp_git_repo: Path) -> None:
    paths = create_task(tmp_git_repo, "fix-api", valid_manifest())
    assert paths.worktree.joinpath(".git").is_file()
    assert json.loads(paths.state.read_text())["status"] == "created"

def test_acquire_lock_rejects_second_runner(tmp_path: Path) -> None:
    paths = TaskPaths.for_task(tmp_path, "fix-api")
    acquire_lock(paths)
    with pytest.raises(LoopError, match="already running"):
        acquire_lock(paths)
```

- [ ] **Step 2: 运行失败测试**

Run: `python -B -m pytest tests/test_init_task.py -q`

Expected: FAIL，提示 `create_task` 或 `acquire_lock` 未定义。

- [ ] **Step 3: 仅实现初始化与锁**

```python
def create_task(repo: Path, task_id: str, manifest: dict[str, Any]) -> TaskPaths:
    paths = TaskPaths.for_task(repo.resolve(), task_id)
    if paths.root.exists():
        raise LoopError(f"task already exists: {task_id}")
    run(["git", "worktree", "add", "-b", f"codex/qoder-{task_id}", str(paths.worktree)], cwd=repo)
    write_json(paths.manifest, manifest)
    write_json(paths.state, {"status": "created", "iteration": 0, "base_commit": git(paths.worktree, "rev-parse", "HEAD")})
    append_event(paths, "task_created", {"worktree": str(paths.worktree)})
    return paths

def acquire_lock(paths: TaskPaths) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    try:
        paths.root.joinpath("runner.lock").open("x", encoding="utf-8").write(str(os.getpid()))
    except FileExistsError as exc:
        raise LoopError("task is already running") from exc
```

使用 `try/finally` 删除本进程创建的锁；若发现陈旧锁，检查记录 PID 是否存在，仅在不存在时将其重命名为带时间戳的审计文件。

- [ ] **Step 4: 验证通过**

Run: `python -B -m pytest tests/test_init_task.py -q`

Expected: PASS。

- [ ] **Step 5: 记录验证证据**

将通过的命令、临时 Git worktree 路径与 pytest 输出摘要写入 `validation/task-2.md`。

### Task 3: 计量采集与可调查汇总

**Files:**
- Create: `C:\Users\LK\.codex\skills\codex-qoder-delivery-loop\scripts\collect_usage.py`
- Create: `C:\Users\LK\.codex\skills\codex-qoder-delivery-loop\tests\test_collect_usage.py`

**Interfaces:**
- Produces: `capture_qoder_usage(paths, sequence, command)`, `summarize_usage(paths)`。
- Consumes: `TaskPaths`、`run`，以及 manifest 的 `qoder_usage_command`。

- [ ] **Step 1: 写失败测试，验证 Qoder `/usage` 差值和未知值**

```python
def test_summary_uses_qoder_credits_snapshot_delta(tmp_path: Path) -> None:
    write_snapshot(tmp_path, "qoder-before-1.json", {"credits": 100})
    write_snapshot(tmp_path, "qoder-after-1.json", {"credits": 93})
    assert summarize_usage(paths_for(tmp_path))["qoder"]["credits"] == 7

def test_summary_marks_missing_usage_unavailable(tmp_path: Path) -> None:
    assert summarize_usage(paths_for(tmp_path))["qoder"]["credits"] == "unavailable"
```

- [ ] **Step 2: 运行失败测试**

Run: `python -B -m pytest tests/test_collect_usage.py -q`

Expected: FAIL，提示计量模块不存在。

- [ ] **Step 3: 实现快照和汇总**

```python
def capture_qoder_usage(paths: TaskPaths, sequence: int, command: list[str], phase: str) -> Path:
    result = run(command, cwd=paths.worktree, timeout_seconds=30)
    raw = paths.root / "usage" / f"qoder-{phase}-{sequence}.txt"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(result.stdout + result.stderr, encoding="utf-8")
    append_event(paths, "usage_snapshot", {"provider": "qoder", "phase": phase, "path": str(raw)})
    return raw

def qoder_credit_delta(before: dict[str, Any], after: dict[str, Any]) -> int | str:
    before_value, after_value = before.get("credits"), after.get("credits")
    if isinstance(before_value, (int, float)) and isinstance(after_value, (int, float)) and before_value >= after_value:
        return before_value - after_value
    return "unavailable"
```

解析器必须接受 JSON 或文本输出中明确标记的 Credits 数值；无法无歧义解析时保留原文并返回 `unavailable`。默认 usage 调用为可配置的 `qoder_usage_command`；默认值在文档中注明为 `qoderclicn --print /usage`，以便环境需要时覆盖。

- [ ] **Step 4: 验证通过**

Run: `python -B -m pytest tests/test_collect_usage.py -q`

Expected: PASS。

- [ ] **Step 5: 记录验证证据**

将 `/usage` 快照替身输入、解析结果和 pytest 输出摘要写入 `validation/task-3.md`。

### Task 4: 实现 Qoder 执行、测试、审查与状态机

**Files:**
- Create: `C:\Users\LK\.codex\skills\codex-qoder-delivery-loop\scripts\run_task.py`
- Create: `C:\Users\LK\.codex\skills\codex-qoder-delivery-loop\tests\test_run_task.py`
- Modify: `C:\Users\LK\.codex\skills\codex-qoder-delivery-loop\scripts\common.py`

**Interfaces:**
- Consumes: `load_manifest`, `capture_qoder_usage`, `summarize_usage`, `acquire_lock`。
- Produces: `run_task(paths, auto: bool) -> int`，最终状态为 `delivered`、`blocked` 或已命名失败终态。

- [ ] **Step 1: 写失败测试，覆盖每轮一提交、范围和终态**

```python
def test_round_rejects_zero_or_multiple_qoder_commits(fake_task: TaskPaths) -> None:
    with pytest.raises(LoopError, match="exactly one commit"):
        validate_round_commit(fake_task.worktree, "base", "head-with-two-commits")

def test_terminal_task_does_not_run_qoder_again(fake_task: TaskPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    write_json(fake_task.state, {"status": "delivered", "iteration": 1})
    monkeypatch.setattr(run_task_module, "run", lambda *args, **kwargs: pytest.fail("must not call CLI"))
    assert run_task(fake_task, auto=True) == 2

def test_scope_violation_stops_before_tests(fake_task: TaskPaths) -> None:
    assert run_task(fake_task, auto=False) == 2
    assert state(fake_task)["status"] == "scope_violation"
```

- [ ] **Step 2: 运行失败测试**

Run: `python -B -m pytest tests/test_run_task.py -q`

Expected: FAIL，提示运行器或验证函数不存在。

- [ ] **Step 3: 实现最小状态机**

```python
def validate_round_commit(worktree: Path, start: str, head: str) -> None:
    if git_exit(worktree, "merge-base", "--is-ancestor", start, head) != 0:
        raise LoopError("Qoder rewrote history")
    count = int(git(worktree, "rev-list", "--count", f"{start}..{head}"))
    if count != 1:
        raise LoopError("Qoder must create exactly one commit")

def run_task(paths: TaskPaths, auto: bool) -> int:
    state = read_state(paths)
    if state["status"] not in {"created", "blocked"}:
        return 2
    with task_lock(paths):
        return run_until_review(paths, auto)
```

`run_until_review` 必须按 `planned → implementing → testing → reviewing` 写事件和状态；在每次 Qoder 实现调用前后采集 usage，保存完整提示词和 stdout/stderr；验证工作区干净、改动相对基线位于白名单内、运行 manifest 测试命令，最后调用 `codex review --base <base>`。审查首行仅接受 `LOOP_VERDICT: APPROVED` 或 `LOOP_VERDICT: BLOCKED`；前者写 `delivered` 和 `final-report.md`，后者仅在 `auto=True` 时进入下一轮。

- [ ] **Step 4: 验证通过**

Run: `python -B -m pytest tests/test_run_task.py -q`

Expected: PASS。

- [ ] **Step 5: 记录验证证据**

将替身 Qoder/Codex 的命令记录、状态转移与 pytest 输出摘要写入 `validation/task-4.md`。

### Task 5: 编写 skill 指令、任务调查器与端到端验证

**Files:**
- Create: `C:\Users\LK\.codex\skills\codex-qoder-delivery-loop\scripts\inspect_task.py`
- Create: `C:\Users\LK\.codex\skills\codex-qoder-delivery-loop\references\manifest-schema.md`
- Modify: `C:\Users\LK\.codex\skills\codex-qoder-delivery-loop\SKILL.md`
- Modify: `C:\Users\LK\.codex\skills\codex-qoder-delivery-loop\agents\openai.yaml`
- Create: `C:\Users\LK\.codex\skills\codex-qoder-delivery-loop\tests\test_end_to_end.py`

**Interfaces:**
- Consumes: 任务目录中的 `state.json`、`events.jsonl`、usage 汇总和 Git 元数据。
- Produces: CLI 调查输出与最终交付报告的固定段落。

- [ ] **Step 1: 写失败端到端测试，验证两个任务隔离与报告计量**

```python
def test_two_tasks_have_isolated_worktrees_and_reports(tmp_git_repo: Path) -> None:
    first = create_task(tmp_git_repo, "fix-api", valid_manifest())
    second = create_task(tmp_git_repo, "fix-ui", valid_manifest())
    assert first.worktree != second.worktree
    assert inspect_task(first)["task_id"] == "fix-api"
    assert inspect_task(second)["task_id"] == "fix-ui"

def test_final_report_contains_per_provider_usage(fake_delivered_task: TaskPaths) -> None:
    report = fake_delivered_task.root.joinpath("final-report.md").read_text(encoding="utf-8")
    assert "Codex" in report and "Qoder" in report
    assert "Credits" in report or "unavailable" in report
```

- [ ] **Step 2: 运行失败测试**

Run: `python -B -m pytest tests/test_end_to_end.py -q`

Expected: FAIL，提示调查器或报告生成器不存在。

- [ ] **Step 3: 实现调查器和精简 skill 指令**

```python
def inspect_task(paths: TaskPaths) -> dict[str, Any]:
    state = json.loads(paths.state.read_text(encoding="utf-8"))
    return {
        "task_id": paths.root.name,
        "status": state["status"],
        "base_commit": state.get("base_commit"),
        "head": git(paths.worktree, "rev-parse", "HEAD"),
        "usage": summarize_usage(paths),
        "next_action": next_action_for(state["status"]),
    }
```

`SKILL.md` 必须使用祈使语气：Codex 先写设计与验收契约，再调用初始化器、要求 Qoder 计划/实现/测试、运行审查并交付；不得让 Qoder 负责设计或最终批准。`agents/openai.yaml` 由 `generate_openai_yaml.py` 生成，display name 为“Codex Qoder 交付循环”。

- [ ] **Step 4: 运行全部测试和 skill 校验**

Run: `python -B -m pytest tests -q`

Expected: PASS。

Run: `python D:\skills\.system\skill-creator\scripts\quick_validate.py C:\Users\LK\.codex\skills\codex-qoder-delivery-loop`

Expected: skill 验证通过。

- [ ] **Step 5: 记录验证证据**

将完整 pytest 输出、`quick_validate.py` 输出和最终 skill 路径写入 `validation/task-5.md`。

## 自检

- 设计文档的角色边界、并行隔离、审计、状态机、范围保护、测试、Credits 和 `unavailable` 均分别由 Task 1–5 覆盖。
- 不含待定事项、空实现说明或“后续再实现”一类占位语。
- `TaskPaths`、`create_task`、`capture_qoder_usage`、`run_task` 和 `inspect_task` 的输入输出在后续任务中保持一致。
