# StockPred P1 可靠性修复设计

## 范围

本设计仅处理两个 P1：

1. 分文件 OHLCV 中断发布后，详情摘要仍可能由部分 CSV 推断不完整的 chart_symbols。
2. 批次 POST 成功而详情暂不可用时，用户仍可能重复点击，启动多次昂贵回测。

已删除的旧版报告不做兼容或迁移。本设计不改变回测算法、Parity/Research 参数规则和 SSE 事件语义。

## 目标

- 对有 detail_manifest.json 的运行，所有 OHLCV 消费入口只在完成标记有效时读取 CSV。
- 发布未完成时，摘要返回 manifest 的完整 codes，绝不从部分 CSV 推断标的集。
- 同一用户启动意图的任意重试只创建、执行一个 batch。
- 不同幂等键仍可主动创建不同 batch。

## 详情发布门闩

### 状态

| 状态 | 判定 | CSV 可读 | 摘要标的来源 |
|---|---|---|---|
| 非分阶段运行 | 无 detail_manifest.json | 保持现有行为 | 保持现有行为 |
| 已完成 | marker、digest、codes 和全量 CSV 一致 | 是 | CSV / price_series |
| 未完成或损坏 | 有 manifest 但任一校验失败 | 否 | manifest codes，再回退 context codes |

新增共享纯函数 detail_publish_complete(run_dir: Path) -> bool。它解析 detail_manifest.json，并校验 detail_complete.json 的 version、codes、manifest SHA-256 与每个 ohlcv_<code>.csv。所有读取方使用该函数；不得在 ui_services.py 另写一套校验规则。

### 数据流

1. materialize_strategy_detail 为每次调用创建 .detail.staging.<uuid4>。
2. 全部 CSV 先写入该 staging。
3. 逐文件发布到 artifacts。
4. 以 .detail_complete.<uuid4>.tmp 写 marker，再用 replace 原子发布 marker。
5. 仅 marker 有效后，消费者才能读取 OHLCV。

若第 1 至 4 步失败，可能有遗留 CSV，但没有有效 marker；价格读取忽略它们，摘要从 manifest 返回完整 codes。

### 读取规则

- load_price_series：仅在非分阶段运行或 detail_publish_complete 为真时读取已有分文件 OHLCV；否则走现有 lazy materialize / reconstruct。
- load_chart_symbols：对未完成的分阶段运行，直接返回 manifest codes；不得扫描 ohlcv_*.csv。
- 非分阶段运行保持现有扫描与回退行为。

UUID 临时路径修复同一 FastAPI 进程多个线程共享 PID 的 staging 冲突，也修复固定 marker 临时文件竞争。本次不引入发布锁：并发调用可重复计算，但有效 marker 仍保证不暴露部分结果。

## 批次创建幂等

### API 契约

POST /stockpred/strategy-batches 增加传输字段：

    { "idempotency_key": "<UUID>", ...现有请求字段 }

- idempotency_key 由 Pydantic 校验为 UUID。
- 它是调度元数据，不属于 StrategyBatchRequest，不参与 comparison_key，也不写入策略回测配置。
- 同 key 返回同一 batch_id 与 events_url；不同 key 是独立用户意图。

### 原子预留

StockPredBatchStore 增加 reserve_idempotent：

1. 创建完整但尚未执行的候选 batch 目录，写入 request、state、reports 和 manifest。
2. 使用 open(mapping, "x") 原子创建 strategy_batches/.idempotency/<key>.json，其内容为候选 batch_id。
3. 抢到映射的调用返回 (candidate_id, True)；路由只在 True 时启动后台 execute。
4. 映射已存在时读取 batch_id，删除本调用刚创建且未认领的候选目录，返回 (existing_id, False)。

映射只在候选目录完整存在后发布，因此并发重试拿到的 batch 一定可 GET/SSE。损坏映射或映射指向不存在 batch 时，POST 明确失败，绝不创建第二个执行任务。

### 前端状态

- 用户一次点击生成 crypto.randomUUID()，保存在 useRef。
- 同次 POST 网络重试、详情 GET 重试与渲染都复用该 key。
- POST 成功后设置 active batch；它处于 queued、running 或 stalled 时启动按钮禁用。
- 收到 done 或 batch_error，或 GET 确认终态时，清空 active batch 与 key。
- 后端幂等是正确性边界；前端禁用只减少误操作。

## TDD 验收

| 场景 | 断言 |
|---|---|
| 部分 CSV、无 marker | load_price_series 不返回部分行；load_chart_symbols 返回 manifest 全量 codes |
| digest 不同或缺少 CSV | 两个读取入口均视为未完成 |
| marker 完整 | 全量行情与全量标的可读 |
| 两次物化 | staging 与 marker 临时路径均不同 |
| 同 key 两次 POST | 同一 batch_id，execute 只调一次 |
| 不同 key、同参数 | 两个 batch_id，均可执行 |
| POST 成功、GET 连续失败 | 不显示启动失败，按钮禁用 |
| 收到终态 | 按钮恢复，下一次点击使用新 key |

