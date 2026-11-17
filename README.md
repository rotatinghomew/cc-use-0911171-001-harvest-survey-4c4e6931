# harvest-survey

合作社地块名册、采收观察及供货承诺核对。

## 当前产品

这是可独立运行的本地分析应用，提供业务记录核对和确定性基线结果。各业务职责可直接作为 Python 接口调用，`app.py` 负责组合一次只读业务检查。结果可保存为 JSON，不依赖远端服务。

- `frame.py`：名册、抽样机会及实际观察覆盖。
- `harvest.py`：采收进度与地块观测的有效口径。
- `commitments.py`：订单间共享可供量和履约承诺（仅登记实产口径）。
- `estimate.py`：面向供货日的产量区间估计，区分已收/待收/缺口/晚收，按抽样概率加权。
- `risk.py`：共同减产情景下的接单组合比较与短供解释。
- `analyses.py`：内容哈希编号、不可变分析包与离线重放。

## 运行

使用 Python 3.11.14，无需安装第三方包。

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q .
python3 app.py demo
```

`inspect` 与 `demo` 使用相同业务入口；`--as-of` 选择已知日期，`--data` 可选本地数据文件，`--output` 可将本次业务核对结果写入文件。默认数据位于 `fixtures/data.json`，默认日期为 `2026-11-17`。这些命令仅展示当前已经存在的能力。

### 产量估计与履约风险

```bash
python3 app.py estimate --due-on 2026-11-20 --known-at 2026-11-17
python3 app.py estimate --due-on 2026-11-22 --stress 0.9 0.8 --confirmed-only
python3 app.py replay --analysis-id ANA-xxxxxxxxxxxxxxxx
```

`estimate` 只使用 `--known-at` 时有效的名册、抽样安排与采收进度，输出覆盖说明、总体及村组的已收实产与待收区间、缺口/晚收面积、共同减产情景和各接单组合的履约风险。默认把不可变分析包写入 `out/analyses/`（可用 `--store` 改目录、`--no-store` 关闭）；`--stress` 自定义共同减产系数，`--confirmed-only` 排除意向订单。分析编号是对快照、参数与方法版本的内容哈希，无随机数与墙钟时间：补录和名册调整不会改动已发出的分析包，只会产生可比较的新编号；`replay` 仅凭分析包内快照重现当时判断。

## 容器

`Dockerfile` 固定到 Python 基础镜像内容摘要。构建后运行不联网：

```bash
docker build -t harvest-survey:local .
docker run --rm --network none harvest-survey:local python -m unittest discover -s tests -v
docker run --rm --network none harvest-survey:local
```

## 数据与业务契约

样例记录全部为本地合成数据，不代表真实农户、市场成交或实际试验结果。日期采用 `YYYY-MM-DD`，有记录时点的条目以 `recorded_at` 表示系统已知日期；同一业务键存在多个修订时选已知范围内最新记录，同日由 `revision` 区分，`withdrawn` 表示撤回。可修改样例验证业务边界，不能将演示数据的统计方向视为真实结论。

plots的有效日期区间左闭右开；visits的harvested值是地块累计观察而非增量。同地块后一次有效观察替代前一次进度。抽样概率记录于名册，面积单位为亩、重量为kg。订单group为空表示可跨村组供货，非空时限定来源。订单等记录可缺省recorded_at，视为始终已知。承诺核对（`commitments`）仅使用登记实产，不做抽样外推；产量估计（`estimate`）另按以下口径处理。

## 产量估计与风险口径

- 抽样单元是地块，不是走访：同一地块连续观察只计一个样本，后一次有效观察替代前一次。
- 估计域为村组×地力带（soil_band）。亩产率中心点采用 1/π 加权的比率估计 Σ(kg/π)/Σ(面积/π)，修正示范田多抽造成的不等概率偏差；同时保留简单平均值作为对照，朴素"平均亩产×全村面积"只在 `method.reference_calculation` 中展示，不参与承诺。
- 已收 `registered_kg` 是登记实产；待收 `pending` 只给区间，下界/上界取该估计域内实测亩产率的最小/最大值，中心点优先用本地块已收部分实测率。已收与待收始终分行呈现，不相加伪装成确定产量。
- 无任何观察且无排期的地块为 `unscheduled` 缺口；所在村组×地力带没有任何实测亩产率时为 `unobserved_domain` 缺口（指示量为 null）；`expected_finish` 晚于供货日的待收面积为 late。三类都不计入安全可供量，只保留可追溯的指示性数字。
- 下界/上界/压力情景是相关冲击：同一减产因素同时施加到全部待收地块，区间是同步边界，不是各村组独立波动后相加。风险分析逐单扣减共享库存，枚举意向订单的所有接单组合，短供解释说明争用、村组限定、缺口与晚收各占多少。

接口细节见 `docs/contracts.md`。测试使用各自的内存副本和临时目录，不修改原始数据或依赖固定端口。
