# 业务数据契约

plots的有效日期区间左闭右开；visits的harvested值是地块累计观察而非增量。同地块后一次有效观察替代前一次进度。抽样概率记录于名册，面积单位为亩、重量为kg。订单group为空表示可跨村组供货，非空时限定来源。订单可缺省recorded_at（视为始终已知）。承诺核对仅使用登记实产，不做抽样外推；产量估计见下。

## 现有组合入口

`app.inspect(data, as_of)` 是只读检查，输入为本地记录集合，输出为 JSON 可表示的字典；不修改输入。领域接口会对不符合业务口径的数据抛出 `ValueError`。缺失、不足和不适用不应伪装成零数量。

## 产量估计入口

- `estimate.yield_outlook(data, due_on, known_at, stress_factors=...)`：返回覆盖说明（走访条数与抽样地块数分列）、估计域亩产率表（简单均值与 1/π 加权均值并列）、各村组与总体的 `registered_kg`/`pending`/`gap`/`late`/`available` 区间，以及同步情景 `scenarios`。
- `risk.evaluate_risk(data, due_on, known_at, outlook, include_proposed=True)`：枚举 confirmed 固定、proposed 可选的接单组合，每个组合在 lower_bound/central/upper_bound/stress_* 情景下逐单分配共享库存，给出 `shortfall_kg` 与 `shortfall_explanation`。
- `analyses.build_analysis(data, due_on, known_at, ...)`：组装含快照的不可变分析包，`analysis_id` 为 `ANA-` 加内容哈希前 16 位；`issue_analysis` 幂等落盘，`replay` 只用包内快照重算并核对编号与结果。

## 不可推断与缺口约定

- `unscheduled`：地块从未观察，没有自己的亩产率与采收排期。
- `unobserved_domain`：该地块所在村组×地力带没有任何实测亩产率，指示量为 `null`，不得外推。
- `expected_finish_after_due_on`：有排期但晚于供货日，不计入当日可供量。
- 缺口与晚收面积只在 `gap`/`late` 明细与 `gap_mu`/`late_mu` 中披露，永远不进入 `available` 与任何风险情景的可供量。

## 重现性约定

分析包固化 `known_at` 时刻有效的 plots/visits/orders 快照（含撤回过滤与修订选择）、`estimator_version`、参数和三类记录的指纹。`known_at` 之后的补录、名册调整对该分析包不可见；同依据同参数必然产生同一 `analysis_id`，篡改快照会使 `replay` 抛出 `ValueError`。新数据产生的新分析与旧分析并列存储，可直接比较。
