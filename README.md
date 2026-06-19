# Tquant Core — 回测引擎与策略框架

Tquant 量化工作站的回测与策略执行核心，支持单股/多股回测、自定义策略、模拟交易。

> 这是 [Tquant](https://github.com/TTL-966/tquant)（私有仓库）的公开组件提取。完整项目含选股、图表、实时行情、自动交易等功能。

## 功能

- **策略回测** — 单股/多股历史数据回测，含基准对比（沪深300）
- **多股并发** — QThread 异步回测，支持取消与进度回调
- **策略引擎** — 基于策略配置（JSON）的信号生成与订单管理
- **交易模拟** — 模拟撮合（涨跌停/停牌检查），计算收益率/夏普比率/最大回撤

## 文件说明

| 文件 | 用途 |
|------|------|
| `backend/backtest_executor.py` | 单股回测执行器（核心算法） |
| `backend/multi_backtest_executor.py` | 多股回测执行器 |
| `backend/backtest_worker.py` | QThread 异步回测 Worker |
| `backend/backtest_job_manager.py` | 回测任务管理与取消 |
| `backend/strategy_engine.py` | 策略信号生成引擎 |
| `backend/trade_simulation.py` | 模拟交易撮合 |
| `backend/data_feed.py` | K线数据供给（多周期自适应） |
| `backend/db.py` | SQLite 数据库查询封装 |
| `backend/strategy_storage.py` | 策略 JSON 持久化 |

## 技术栈

- Python 3.12+
- NumPy, Pandas
- SQLAlchemy (SQLite)
- PySide6 (QThread)

## 依赖

此仓库依赖 Tquant 完整项目的其他模块，不能独立运行：

- `backend/config_manager.py` — 配置加载
- `backend/data_updater/` — 数据更新
- Tushare / BaoStock 数据源

代码供学习回测引擎设计参考。完整可运行项目见 [Tquant](https://github.com/TTL-966/tquant)（私有）。

## 回测指标

- 累计收益率 / 年化收益率 / 夏普比率
- 最大回撤 / 胜率 / 盈亏比 / Alpha / Beta

## Tquant 完整功能

Tquant 是基于 PySide6 + QtWebEngine 的桌面端量化交易平台，覆盖 K线图表、股票筛选、策略回测、自动交易、数据更新。

## 协议

MIT
