"""Backtest worker: runs backtest in QThread, emits progress and result via signals."""

import traceback

from PySide6.QtCore import QThread, Signal

from backend.data_feed import DataFeed
from backend.backtest_executor import BacktestExecutor
from backend.multi_backtest_executor import MultiBacktestExecutor


class BacktestWorker(QThread):
    """Runs a backtest job in a background thread.

    Signals:
        progress(current, total)  — emitted after each bar/date
        finished(result_dict)     — emitted on completion (success or error)
    """

    progress = Signal(int, int)
    finished = Signal(dict)

    def __init__(self, params, parent=None):
        super().__init__(parent)
        self.params = params
        self._executor_ref = None

    def run(self):
        try:
            p = self.params
            mode = p.get("mode", "single")

            if mode == "compare":
                result = self._run_compare(p)
            elif mode == "multi":
                result = self._run_multi(p)
            else:
                result = self._run_single(p)

            self.finished.emit(result)
        except Exception as e:
            traceback.print_exc()
            self.finished.emit({
                "success": False,
                "error": str(e),
                "signals": [],
                "equity_curve": [],
                "metrics": {},
                "logs": [f"[ERROR] Worker crashed: {e}"],
            })

    def cancel(self):
        """Request cancellation of the running backtest."""
        if self._executor_ref:
            self._executor_ref._cancelled = True
        self.requestInterruption()

    # ── single stock ──

    def _run_single(self, p):
        data_feed = DataFeed()
        executor = BacktestExecutor(data_feed)
        self._executor_ref = executor

        def on_progress(idx, total):
            if self.isInterruptionRequested():
                executor._cancelled = True
            self.progress.emit(idx, total)

        result = executor.run(
            user_code=p["code"],
            stock_code=p["stock"],
            start_date=p.get("start", "2010-01-01"),
            end_date=p.get("end", "2026-12-31"),
            initial_cash=p.get("cash", 1000000),
            slippage=p.get("slippage", "close"),
            commission_rate=p.get("commission_rate", 0.0003),
            stamp_tax_rate=p.get("stamp_tax_rate", 0.001),
            slippage_cost_type=p.get("slippage_cost_type", "percent"),
            slippage_cost_value=p.get("slippage_cost_value", 0.1),
            benchmark_code=p.get("benchmark_code"),
            progress_callback=on_progress,
        )

        result["success"] = (result.get("status") != "error")
        if "status" in result:
            del result["status"]
        return result

    # ── multi stock ──

    def _run_multi(self, p):
        data_feed = DataFeed()
        executor = MultiBacktestExecutor(data_feed)
        self._executor_ref = executor

        def on_progress(idx, total):
            if self.isInterruptionRequested():
                executor._cancelled = True
            self.progress.emit(idx, total)

        result = executor.run(
            user_code=p["code"],
            stock_codes=p.get("stocks", []),
            start_date=p.get("start", "2010-01-01"),
            end_date=p.get("end", "2026-12-31"),
            initial_cash=p.get("cash", 1000000),
            slippage=p.get("slippage", "close"),
            commission_rate=p.get("commission_rate", 0.0003),
            stamp_tax_rate=p.get("stamp_tax_rate", 0.001),
            slippage_cost_type=p.get("slippage_cost_type", "percent"),
            slippage_cost_value=p.get("slippage_cost_value", 0.1),
            benchmark_code=p.get("benchmark_code"),
            progress_callback=on_progress,
        )

        return result

    # ── compare (multi-variation) ──

    def _run_compare(self, p):
        from concurrent.futures import ThreadPoolExecutor, as_completed

        variations = p.get("variations", [])
        stock_pool = p.get("stock_pool", [p.get("stock", "000001")])
        use_multi = p.get("use_multi", len(stock_pool) > 1)
        total_vars = len(variations)

        results = []
        errors = []

        def run_one(variation):
            name = variation.get("name", "未命名")
            code = variation.get("code", "")
            if not code:
                return (name, None, "策略代码为空")

            try:
                data_feed = DataFeed()
                if use_multi:
                    executor = MultiBacktestExecutor(data_feed)
                    result = executor.run(
                        code, stock_pool,
                        start_date=p.get("start", "2010-01-01"),
                        end_date=p.get("end", "2026-12-31"),
                        initial_cash=p.get("cash", 1000000),
                        slippage=p.get("slippage", "close"),
                        commission_rate=p.get("commission_rate", 0.0003),
                        stamp_tax_rate=p.get("stamp_tax_rate", 0.001),
                        slippage_cost_type=p.get("slippage_cost_type", "percent"),
                        slippage_cost_value=p.get("slippage_cost_value", 0.1),
                        benchmark_code=p.get("benchmark_code"),
                    )
                else:
                    executor = BacktestExecutor(data_feed)
                    result = executor.run(
                        code, stock_pool[0],
                        start_date=p.get("start", "2010-01-01"),
                        end_date=p.get("end", "2026-12-31"),
                        initial_cash=p.get("cash", 1000000),
                        slippage=p.get("slippage", "close"),
                        commission_rate=p.get("commission_rate", 0.0003),
                        stamp_tax_rate=p.get("stamp_tax_rate", 0.001),
                        slippage_cost_type=p.get("slippage_cost_type", "percent"),
                        slippage_cost_value=p.get("slippage_cost_value", 0.1),
                        benchmark_code=p.get("benchmark_code"),
                    )

                if not result.get("success") and "error" in result:
                    return (name, None, result["error"])

                return (name, {
                    "name": name,
                    "equity_curve": result.get("equity_curve", []),
                    "metrics": result.get("metrics", {}),
                    "signals": result.get("signals", []),
                    "logs": result.get("logs", []),
                    "stock_performance": result.get("stock_performance", []),
                    "benchmark_equity_curve": result.get("benchmark_equity_curve"),
                    "benchmark_code": result.get("benchmark_code"),
                    "errors": result.get("errors", []),
                }, None)
            except Exception as e:
                traceback.print_exc()
                return (name, None, str(e))

        max_workers = min(total_vars, 5)
        completed = 0
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(run_one, v): v for v in variations}
            for future in as_completed(futures):
                if self.isInterruptionRequested():
                    for f in futures:
                        f.cancel()
                    break
                name, data, err = future.result()
                completed += 1
                self.progress.emit(completed, total_vars)
                if err:
                    errors.append({"name": name, "error": err})
                if data:
                    results.append(data)

        return {
            "success": len(results) > 0,
            "results": results,
            "errors": errors,
        }
