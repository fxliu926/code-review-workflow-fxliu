"""Workflow 引擎：节点 + 状态 + 跳转。

相比初版的改动（对应作业「推荐实现」里的流程控制 / 执行日志 / 错误恢复）：

- 跳转条件改用 `workflow/expr.py` 的受限求值器，替换原来的 `eval()`
- 加载 YAML 时做完整校验：字段缺失、节点 id 重复、Agent 未注册、跳转目标不存在
- 节点级错误恢复：可配置重试次数与失败策略（abort / skip / goto）
- 全局步数上限，防止配置成环时无限循环
- 支持并行节点：一个节点的多个分支同时执行，再合并结果
- 全程写入 `workflow/trace.py` 的 Trace，可落盘 jsonl + Markdown 报告
"""
import copy
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

from workflow.expr import SafeExprError, evaluate
from workflow.trace import Trace, diff_state, make_run_dir

DEFAULT_MAX_STEPS = 50


class WorkflowError(ValueError):
    """Workflow 配置或运行的通用错误。"""


class ConfigError(WorkflowError):
    """流程配置不合法。"""


class StepLimitExceeded(WorkflowError):
    """执行步数超过上限，疑似配置成环。"""


class NodeExecutionError(RuntimeError):
    """节点执行失败且策略为中断。"""


class WorkflowEngine:
    def __init__(self, trace: Trace = None, max_steps: int = DEFAULT_MAX_STEPS,
                 max_node_retries: int = 1, retry_backoff: float = 1.0,
                 verbose: bool = True):
        self.nodes = {}
        self.start = None
        self.max_steps = max_steps
        self.max_node_retries = max_node_retries
        self.retry_backoff = retry_backoff
        self.verbose = verbose
        self.trace = trace
        self.last_trace = None

    # ---------- 注册与加载 ----------

    def add_node(self, node_id: str, fn, next_node, retries: int = None,
                 on_error="abort", parallel: list = None, merge=None):
        """注册一个节点。

        next_node：下一个节点 id、"done"，或一个接收 state 返回节点 id 的函数。
        retries：失败后的额外重试次数（None 表示跟随引擎的 max_node_retries）。
        on_error：失败策略，abort（中断）/ skip（跳过继续）/ {"goto": "节点id"}。
        parallel：并行分支的节点 id 列表；给了它，本节点就当作并行容器，
                  自己的 fn 不再执行。
        merge：并行结束后的合并函数 `fn(state) -> state`，用来重算跨分支的派生字段。
        """
        if on_error not in ("abort", "skip") and not isinstance(on_error, dict):
            raise ConfigError(f"不支持的 on_error：{on_error}")
        if isinstance(on_error, dict) and "goto" not in on_error:
            raise ConfigError("on_error 为字典时必须含 goto")
        self.nodes[node_id] = {
            "fn": fn, "next": next_node, "retries": retries, "on_error": on_error,
            "parallel": parallel, "merge": merge,
        }

    def is_parallel(self, node_id: str) -> bool:
        return bool(self.nodes.get(node_id, {}).get("parallel"))

    def load_from_yaml(self, path: str, agent_registry: dict):
        """从 YAML 加载节点定义，并在加载期做完整校验（fail fast）。"""
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        if not isinstance(config, dict):
            raise ConfigError("配置文件根节点必须是映射")

        start = config.get("start")
        specs = config.get("nodes")
        if not start:
            raise ConfigError("缺少 start 字段")
        if not specs or not isinstance(specs, list):
            raise ConfigError("缺少 nodes 列表")

        node_ids = []
        for spec in specs:
            if not isinstance(spec, dict) or "id" not in spec:
                raise ConfigError(f"节点定义缺少 id：{spec}")
            node_ids.append(spec["id"])

        duplicates = sorted({i for i in node_ids if node_ids.count(i) > 1})
        if duplicates:
            raise ConfigError(f"节点 id 重复：{duplicates}")

        self.start = start
        if start != "done" and start not in node_ids:
            raise ConfigError(f"start 指向不存在的节点：{start}")

        for spec in specs:
            node_id = spec["id"]
            parallel = spec.get("parallel")

            # 并行容器本身不执行 Agent，只需要校验它引用的分支都存在
            if parallel is None:
                agent_name = spec.get("agent")
                if not agent_name:
                    raise ConfigError(f"节点 {node_id} 缺少 agent")
                if agent_name not in agent_registry:
                    raise ConfigError(f"节点 {node_id} 引用了未注册的 Agent：{agent_name}")
                fn = agent_registry[agent_name]
            else:
                if not isinstance(parallel, list) or not parallel:
                    raise ConfigError(f"节点 {node_id} 的 parallel 必须是非空列表")
                unknown = [b for b in parallel if b not in node_ids]
                if unknown:
                    raise ConfigError(f"节点 {node_id} 并行分支不存在：{unknown}")
                if any(self.is_parallel_candidate(specs, b) for b in parallel):
                    raise ConfigError(f"节点 {node_id} 的并行分支不能再是并行容器（暂不支持嵌套）")
                fn = None

            merge_name = spec.get("merge")
            if merge_name and merge_name not in agent_registry:
                raise ConfigError(f"节点 {node_id} 引用了未注册的合并函数：{merge_name}")
            merge_fn = agent_registry[merge_name] if merge_name else None

            next_spec = spec.get("next", "done")
            targets = []
            if isinstance(next_spec, list):
                for cond in next_spec:
                    if not isinstance(cond, dict) or "goto" not in cond:
                        raise ConfigError(f"节点 {node_id} 的条件分支缺少 goto")
                    targets.append(cond["goto"])
                next_spec = self._build_conditional_next(next_spec, node_id)
            elif isinstance(next_spec, str):
                if next_spec != "done":
                    targets.append(next_spec)
            else:
                raise ConfigError(f"节点 {node_id} 的 next 只能是字符串或条件列表")

            missing = [t for t in targets if t != "done" and t not in node_ids]
            if missing:
                raise ConfigError(f"节点 {node_id} 跳转到不存在的节点：{missing}")

            self.add_node(node_id, fn, next_spec,
                          retries=spec.get("retries"),
                          on_error=spec.get("on_error", "abort"),
                          parallel=parallel, merge=merge_fn)

    @staticmethod
    def is_parallel_candidate(specs: list, node_id: str) -> bool:
        """判断某个节点定义是否也是并行容器（用于拒绝嵌套）。"""
        for spec in specs:
            if spec.get("id") == node_id:
                return spec.get("parallel") is not None
        return False

    def _build_conditional_next(self, conditions: list, owner: str):
        """把 YAML 的条件列表转成函数；条件都不成立时落到无 when 的兜底分支。"""
        branches = []
        fallback = None
        for cond in conditions:
            if "when" not in cond:
                if fallback is None:
                    fallback = cond["goto"]
                continue
            branches.append((cond["when"], cond["goto"]))

        def decide(state: dict) -> str:
            for expr, goto in branches:
                try:
                    if evaluate(expr, state):
                        return goto
                except SafeExprError as e:
                    raise WorkflowError(f"节点 {owner} 的条件求值失败：{e}") from e
            return fallback if fallback is not None else "done"

        return decide

    # ---------- 运行 ----------

    def _execute_node(self, node: dict, state: dict):
        """执行单个节点，返回 (state, ok, error)。失败时按 retries 重试。"""
        attempts = (node["retries"] if node["retries"] is not None
                    else self.max_node_retries) + 1
        last_error = None
        for attempt in range(attempts):
            try:
                result = node["fn"](state)
                return (state if result is None else result), True, None
            except Exception as e:  # 节点异常必须被引擎接管，否则整条流程崩溃
                last_error = e
                if attempt + 1 < attempts:
                    if self.verbose:
                        print(f"[Workflow] 节点异常，重试 {attempt + 1}/{attempts - 1}：{e}")
                    time.sleep(self.retry_backoff * (2 ** attempt))
        return state, False, f"{type(last_error).__name__}: {last_error}"

    # ---------- 并行执行 ----------

    def _execute_parallel(self, node_id: str, node: dict, state: dict, step: int):
        """并行跑多个分支，合并它们对状态的修改。

        每个分支拿到的是状态的深拷贝，互不干扰；跑完后按声明顺序把各自
        「改动过的字段」合并回主状态（失败的分支不合并，避免半截状态）。
        合并完再调用配置的 merge 函数，重算跨分支的派生字段
        （比如问题计数与是否放行）。

        失败分两层判断：先看分支自己的 on_error —— 配了 skip 的分支挂了就
        当作没跑，不影响整体；配了 abort 的分支挂了，才由容器节点的 on_error
        决定是中断整条流程还是跳过继续。
        """
        branches = node["parallel"]
        snapshot = dict(state)
        trace = self.last_trace
        results = {}

        def run_branch(branch_id):
            branch_node = self.nodes[branch_id]
            started = time.perf_counter()
            state_out, ok, error = self._execute_node(branch_node, copy.deepcopy(state))
            return state_out, ok, error, (time.perf_counter() - started) * 1000

        with ThreadPoolExecutor(max_workers=max(1, len(branches))) as pool:
            futures = {pool.submit(run_branch, b): b for b in branches}
            for future in as_completed(futures):
                branch_id = futures[future]
                try:
                    results[branch_id] = future.result()
                except Exception as e:  # 分支异常不能外溢到线程池
                    results[branch_id] = ({}, False, f"{type(e).__name__}: {e}", 0.0)

        merged = dict(state)
        branch_states = {}
        errors = []
        for branch_id in branches:
            branch_state, ok, error, duration = results.get(
                branch_id, ({}, False, "分支未执行", 0.0))
            branch_states[branch_id] = {"state": branch_state, "ok": ok, "error": error}
            changed = diff_state(snapshot, branch_state)
            if not ok:
                # 分支自己的 on_error 决定这个失败要不要连累整个并行节点。
                # 像"本地规范检查"这种辅助分支配了 skip，它挂了不该拖垮主流程。
                if self.nodes[branch_id]["on_error"] == "abort":
                    errors.append(f"{branch_id}: {error}")
                elif self.verbose:
                    print(f"[Workflow] 并行分支 {branch_id} 失败，按 on_error="
                          f"{self.nodes[branch_id]['on_error']} 忽略：{error}")
            # 默认策略：分支改动过的字段直接覆盖。
            # 需要"累积"的语义（比如两个分支各自报问题）交给 merge 函数处理 ——
            # 引擎不该替业务决定哪些列表要追加，否则上一轮的旧数据会残留到下一轮。
            # 失败的分支可能只写了一半状态，所以只合并跑成功的那些。
            if ok:
                for key in changed:
                    merged[key] = branch_state[key]

            # 每个分支单独记一条轨迹，便于看出它们确实是同时跑的
            if trace is not None:
                trace.record(step, f"{node_id}/{branch_id}", ok, duration, node_id,
                             changed, error=error,
                             round_no=branch_state.get("round"), state=branch_state)

        if node["merge"]:
            try:
                merged = node["merge"](merged, branch_states) or merged
            except TypeError:
                # 兼容只接受 state 的旧式合并函数
                merged = node["merge"](merged) or merged
            except Exception as e:
                errors.append(f"merge: {type(e).__name__}: {e}")

        return merged, not errors, "；".join(errors) or None

    def run(self, state: dict) -> dict:
        """从 start 节点开始跑，直到 done。"""
        trace = self.trace or Trace()
        self.last_trace = trace

        current = self.start
        step = 0
        try:
            while current != "done":
                if step >= self.max_steps:
                    raise StepLimitExceeded(
                        f"执行步数超过上限 {self.max_steps}，请检查配置是否成环")
                if current not in self.nodes:
                    raise WorkflowError(f"节点不存在：{current}")

                step += 1
                node = self.nodes[current]
                if self.verbose:
                    print(f"[Workflow] 执行节点：{current}")

                before = dict(state)
                started = time.perf_counter()
                if node["parallel"]:
                    state, ok, error = self._execute_parallel(current, node, state, step)
                else:
                    state, ok, error = self._execute_node(node, state)
                duration_ms = (time.perf_counter() - started) * 1000

                next_spec = node["next"]
                target = next_spec(state) if callable(next_spec) else (next_spec or "done")

                if not ok:
                    policy = node["on_error"]
                    if policy == "abort":
                        trace.record(step, current, False, duration_ms, "abort",
                                     diff_state(before, state), error=error,
                                     round_no=state.get("round"), state=state)
                        raise NodeExecutionError(f"节点 {current} 执行失败：{error}")
                    if isinstance(policy, dict):
                        target = policy["goto"]
                    # skip：沿用原 target 继续往下走

                trace.record(step, current, ok, duration_ms, target,
                             diff_state(before, state), error=error,
                             round_no=state.get("round"), state=state)
                current = target
            return state
        finally:
            trace.finish(state)

    # ---------- 结果落盘 ----------

    def save_trace(self, base=Path("runs")) -> dict:
        """把本次运行的轨迹写到 runs/<时间戳>-<run_id>/ 下。"""
        if self.last_trace is None:
            raise WorkflowError("尚未运行，没有可保存的轨迹")
        return self.last_trace.save(make_run_dir(base, self.last_trace.run_id))
