# harvest-survey

合作社地块名册、采收观察及供货承诺核对。

## 当前产品

这是可独立运行的本地分析应用，提供业务记录核对和确定性基线结果。各业务职责可直接作为 Python 接口调用，`app.py` 负责组合一次只读业务检查。结果可保存为 JSON，不依赖远端服务。

- `frame.py`：名册、抽样机会及实际观察覆盖。
- `harvest.py`：采收进度与地块观测的有效口径。
- `commitments.py`：订单间共享可供量和履约承诺。

## 运行

使用 Python 3.11.14，无需安装第三方包。

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q .
python3 app.py demo
```

`inspect` 与 `demo` 使用相同业务入口；`--as-of` 选择已知日期，`--data` 可选本地数据文件，`--output` 可将本次业务核对结果写入文件。默认数据位于 `fixtures/data.json`，默认日期为 `2026-11-17`。这些命令仅展示当前已经存在的能力。

## 容器

`Dockerfile` 固定到 Python 基础镜像内容摘要。构建后运行不联网：

```bash
docker build -t harvest-survey:local .
docker run --rm --network none harvest-survey:local python -m unittest discover -s tests -v
docker run --rm --network none harvest-survey:local
```

## 数据与业务契约

样例记录全部为本地合成数据，不代表真实农户、市场成交或实际试验结果。日期采用 `YYYY-MM-DD`，有记录时点的条目以 `recorded_at` 表示系统已知日期；同一业务键存在多个修订时选已知范围内最新记录，同日由 `revision` 区分，`withdrawn` 表示撤回。可修改样例验证业务边界，不能将演示数据的统计方向视为真实结论。

plots的有效日期区间左闭右开；visits的harvested值是地块累计观察而非增量。同地块后一次有效观察替代前一次进度。抽样概率记录于名册，面积单位为亩、重量为kg。订单group为空表示可跨村组供货，非空时限定来源。当前承诺核对仅使用登记实产，不做抽样外推。

接口细节见 `docs/contracts.md`。测试使用各自的内存副本和临时目录，不修改原始数据或依赖固定端口。
