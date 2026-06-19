# backend/multi_backtest_executor.py
# 多股组合回测引擎：共享资金池，按日收集订单，先卖出后买入执行。

import json
import types
import traceback
import numpy as np
import pandas as pd
from backend.backtest_executor import Logger, BacktestExecutor, calculate_benchmark_metrics


class StockHandler:
    """单只股票的策略句柄：持有该股票的 K 线 DataFrame 和沙箱函数。
    订单不立即执行，而是暂存到 shared_context 的 pending_orders 列表中。
    """

    def __init__(self, stock_code, df, shared_context, slippage,
                 commission_rate=0.0003, stamp_tax_rate=0.001,
                 slippage_cost_type="percent", slippage_cost_value=0.1):
        self.stock_code = stock_code
        self.df = df
        self.current_idx = -1
        self._context = shared_context
        self.slippage = slippage
        self.commission_rate = commission_rate
        self.stamp_tax_rate = stamp_tax_rate
        self.slippage_cost_type = slippage_cost_type
        self.slippage_cost_value = slippage_cost_value
        self.daily_functions = []
        self.handle_bar = None
        self.initialize = None

    # ---------- 沙箱注入函数（每只股票独立）----------
    def _attribute_history_wrapper(self, security, count, fields=None):
        if self.df is None or self.current_idx < 0:
            return pd.DataFrame()
        start = max(0, self.current_idx - count)
        end = self.current_idx
        slice_df = self.df.iloc[start:end]
        if fields is not None and isinstance(fields, (list, tuple)):
            cols = ['date'] if 'date' in slice_df.columns else []
            for f in fields:
                if f in slice_df.columns:
                    cols.append(f)
            if not cols:
                return pd.DataFrame()
            slice_df = slice_df[cols]
        return slice_df.reset_index(drop=True)

    def _history_bars_wrapper(self, security, count, unit, field):
        if self.current_idx < 0:
            return np.array([])
        start = max(0, self.current_idx - count + 1)
        end = self.current_idx + 1
        slice_df = self.df.iloc[start:end]
        if slice_df.empty or field not in slice_df.columns:
            return np.array([])
        vals = slice_df[field].values
        return vals[-min(count, len(vals)):]

    def _get_current_data_wrapper(self, security):
        if self.df is None or self.current_idx < 0:
            return {'last_price': 0.0}
        bar = self.df.iloc[self.current_idx]
        return {
            'last_price': bar['close'],
            'open': bar['open'],
            'high': bar['high'],
            'low': bar['low'],
            'close': bar['close'],
        }

    def _order_target_value_wrapper(self, security, value, reason=""):
        """不立即执行，暂存为待处理订单。应用滑点到成交价。"""
        code = BacktestExecutor._normalize_security(security)
        if self.df is None or self.current_idx < 0:
            return
        bar = self.df.iloc[self.current_idx]
        close_price = bar['close']
        if self.slippage == 'next_open' and self.current_idx + 1 < len(self.df):
            fill_price = self.df.iloc[self.current_idx + 1]['open']
        else:
            fill_price = close_price

        portfolio = self._context.portfolio
        holdings = portfolio.get('holdings', {})
        current_shares = holdings.get(code, 0)
        current_value = current_shares * fill_price
        diff_value = value - current_value
        if abs(diff_value) < 0.01:
            return
        shares_to_trade = diff_value / fill_price
        if shares_to_trade > 0:
            shares_to_trade = int(shares_to_trade / 100) * 100
        else:
            shares_to_trade = int(shares_to_trade / 100) * 100
        if shares_to_trade == 0:
            return

        # 应用滑点成本
        if self.slippage_cost_type == "fixed":
            if shares_to_trade > 0:
                fill_price += self.slippage_cost_value
            else:
                fill_price -= self.slippage_cost_value
        elif self.slippage_cost_type == "percent":
            pct = self.slippage_cost_value / 100.0
            if shares_to_trade > 0:
                fill_price *= (1 + pct)
            else:
                fill_price *= (1 - pct)

        # 暂存订单，稍后统一执行
        self._context._pending_orders.append({
            'code': code,
            'shares': shares_to_trade,
            'price': fill_price,
            'reason': reason,
            'commission_rate': self.commission_rate,
            'stamp_tax_rate': self.stamp_tax_rate,
        })

    def _order_target_percent_wrapper(self, security, percent):
        """基于共享总资产计算目标市值。"""
        code = BacktestExecutor._normalize_security(security)
        if self.df is None or self.current_idx < 0:
            return
        bar = self.df.iloc[self.current_idx]
        current_price = bar['close']
        portfolio = self._context.portfolio
        cash = portfolio.get('cash', 0.0)
        holdings = portfolio.get('holdings', {})
        total_holding_value = 0.0
        for h_code, h_shares in holdings.items():
            last_prices = getattr(self._context, '_last_prices', {})
            h_price = last_prices.get(h_code, current_price)
            total_holding_value += h_shares * h_price
        total_assets = cash + total_holding_value
        target_value = total_assets * percent
        reason = getattr(self._context, '_last_signal_reason', '')
        self._order_target_value_wrapper(code, target_value, reason)

    def _run_daily_wrapper(self, func, time='every_bar'):
        # 如果是指数情绪更新函数，注册到共享上下文（每个交易日只执行一次）
        func_name = getattr(func, '__name__', '')
        if func_name.startswith('update_index_cond_'):
            if not hasattr(self._context, '_global_daily_funcs'):
                self._context._global_daily_funcs = []
            existing_names = {getattr(f, '__name__', '') for f in self._context._global_daily_funcs}
            if func_name not in existing_names:
                self._context._global_daily_funcs.append(func)
        else:
            self.daily_functions.append(func)

    def build_sandbox(self, logger, get_index_history=None):
        """构建该股票的沙箱全局命名空间。"""
        sandbox = {
            '__builtins__': __builtins__,
            'pd': pd,
            'np': np,
            'context': self._context,
            'log': logger,
            'attribute_history': self._attribute_history_wrapper,
            'history_bars': self._history_bars_wrapper,
            'order_target_value': self._order_target_value_wrapper,
            'order_target_percent': self._order_target_percent_wrapper,
            'get_current_data': self._get_current_data_wrapper,
            'run_daily': self._run_daily_wrapper,
        }
        if get_index_history:
            sandbox['get_index_history'] = get_index_history
        return sandbox


class MultiBacktestExecutor:
    """多股组合回测执行器：共享资金池，按日收集订单，先卖后买。"""

    def __init__(self, data_source):
        self.data_source = data_source
        self.logs = []
        self.trade_signals = []
        self._index_cache = {}  # 指数数据缓存
        self._cancelled = False

    def run(self, user_code, stock_codes, start_date="2010-01-01", end_date="2026-12-31",
            initial_cash=1000000, slippage="close",
            commission_rate=0.0003, stamp_tax_rate=0.001,
            slippage_cost_type="percent", slippage_cost_value=0.1,
            benchmark_code=None, progress_callback=None):
        """
        :param user_code:   用户策略代码（含 STOCK_CODE_PLACEHOLDER 占位符）
        :param stock_codes: 股票代码列表，如 ["000001", "000858"]
        :param start_date:  起始日期
        :param end_date:    结束日期
        :param initial_cash: 初始资金
        :param slippage:    成交价模式
        :param commission_rate: 佣金率
        :param stamp_tax_rate: 印花税率
        :param slippage_cost_type: 滑点类型
        :param slippage_cost_value: 滑点值
        :return: dict { success, signals, equity_curve, metrics, logs, errors }
        """
        self.logs = []
        self.trade_signals = []
        self._index_cache = {}  # reset cache between runs
        self.slippage = slippage
        self.commission_rate = commission_rate
        self.stamp_tax_rate = stamp_tax_rate
        self.slippage_cost_type = slippage_cost_type
        self.slippage_cost_value = slippage_cost_value

        # ---- 1. 加载所有股票的 K 线数据 ----
        stock_dfs = {}
        for code in stock_codes:
            try:
                raw_str = self.data_source.get_kline_json(code, start_date, end_date, limit=0)
                raw = json.loads(raw_str)
                dates = raw.get('dates', [])
                values = raw.get('values', [])
                if not dates or not values:
                    self.logs.append(f"[WARN] [{code}] K线数据为空，跳过")
                    continue
                cols = ['open', 'close', 'low', 'high', 'volume']
                if values and len(values[0]) >= 6:
                    cols.append('turnover_rate_f')
                df = pd.DataFrame(values, columns=cols)
                df.index = pd.to_datetime(dates)
                df.index.name = 'date'
                df['_code'] = code  # 标记股票代码

                # ---- 检查股票上市/退市状态，调整有效回测区间 ----
                db = getattr(self.data_source, 'db', None)
                if db is not None:
                    status = db.get_stock_status(code)
                    list_date = pd.to_datetime(status['listed'])
                    delist_date = pd.to_datetime(status['delisted']) if status['delisted'] else None

                    actual_start = max(pd.to_datetime(start_date), list_date)
                    actual_end = min(pd.to_datetime(end_date), delist_date) if delist_date else pd.to_datetime(end_date)

                    if actual_start >= actual_end:
                        self.logs.append(
                            f"[WARN] [{code}] 无有效交易日（上市:{status['listed']}, "
                            f"退市:{status.get('delisted')}），回测区间 {start_date}~{end_date}，跳过"
                        )
                        continue

                    df = df[(df.index >= actual_start) & (df.index <= actual_end)]
                    if len(df) < 2:
                        self.logs.append(f"[WARN] [{code}] 有效交易日不足（{len(df)}天），跳过")
                        continue

                    # ---- 检测长期停牌：连续缺失 > 60 个交易日 ----
                    date_diffs = (df.index[1:] - df.index[:-1]).days
                    long_gaps = date_diffs[date_diffs > 90]  # 日历日阈值 ≈60个交易日
                    if len(long_gaps) > 0:
                        max_gap = long_gaps.max()
                        self.logs.append(
                            f"[WARN] [{code}] 存在长期停牌：最大间隔 {max_gap} 天 "
                            f"（共 {len(long_gaps)} 处间隔 > 90 天）"
                        )

                    self.logs.append(
                        f"[INFO] [{code}] 有效区间: {actual_start.strftime('%Y-%m-%d')} ~ "
                        f"{actual_end.strftime('%Y-%m-%d')}, {len(df)}根K线"
                    )
                else:
                    self.logs.append(f"[INFO] [{code}] 加载 {len(df)} 根K线")

                stock_dfs[code] = df
            except Exception as e:
                self.logs.append(f"[ERROR] [{code}] 获取K线失败: {str(e)}")

        if not stock_dfs:
            return self._error_result("所有股票K线数据为空")

        # ---- 2. 对齐到公共日期索引 ----
        all_dates = sorted(set.union(*[set(df.index) for df in stock_dfs.values()]))
        if not all_dates:
            return self._error_result("无公共交易日")

        # 过滤到回测区间
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        common_dates = [d for d in all_dates if start_dt <= d <= end_dt]
        if not common_dates:
            return self._error_result("回测区间内无交易日")

        self.logs.append(f"[INFO] 公共交易日: {len(common_dates)} 天 ({common_dates[0].strftime('%Y-%m-%d')} ~ {common_dates[-1].strftime('%Y-%m-%d')})")

        # ---- 3. 创建共享上下文 ----
        shared_context = types.SimpleNamespace()
        shared_context.portfolio = {
            'cash': initial_cash,
            'holdings': {},   # code -> shares
        }
        shared_context.current_dt = None
        shared_context._pending_orders = []   # 当日待执行订单
        shared_context._last_prices = {}       # 各股票最新收盘价（用于计算总资产）
        shared_context._global_daily_funcs = []  # 全局 daily 函数（指数情绪等，只执行一次）

        # ---- 4. 为每只股票创建 StockHandler 并编译策略 ----
        get_idx_hist = self._make_get_index_history(shared_context, start_date, end_date)
        handlers = {}
        for code, df in stock_dfs.items():
            handler = StockHandler(code, df, shared_context, slippage,
                                    commission_rate=commission_rate, stamp_tax_rate=stamp_tax_rate,
                                    slippage_cost_type=slippage_cost_type, slippage_cost_value=slippage_cost_value)
            # 替换占位符（匹配前端 generateCode 输出的 "STOCK_CODE_PLACEHOLDER" 格式）
            stock_code_str = code.split('.')[0] if '.' in code else code
            code_with_stock = user_code.replace('"STOCK_CODE_PLACEHOLDER"', f'"{stock_code_str}"')
            code_with_stock = code_with_stock.replace("'STOCK_CODE_PLACEHOLDER'", f"'{stock_code_str}'")

            logger = Logger(self)

            sandbox = handler.build_sandbox(logger, get_index_history=get_idx_hist)

            # 编译并执行用户代码
            try:
                code_obj = compile(code_with_stock, f'<strategy_{code}>', 'exec')
            except SyntaxError as e:
                self.logs.append(f"[ERROR] [{code}] 语法错误 (行 {e.lineno}): {e.msg}")
                continue
            except Exception as e:
                self.logs.append(f"[ERROR] [{code}] 编译失败: {str(e)}")
                continue

            try:
                exec(code_obj, sandbox)
            except Exception as e:
                self.logs.append(f"[ERROR] [{code}] 策略执行失败: {str(e)}")
                continue

            initialize = sandbox.get('initialize')
            handle_bar_func = sandbox.get('handle_bar')
            if handle_bar_func is None:
                self.logs.append(f"[WARN] [{code}] 缺少 handle_bar 函数，跳过")
                continue

            handler.initialize = initialize
            handler.handle_bar = handle_bar_func
            handlers[code] = handler

            # 执行 initialize
            if initialize:
                try:
                    initialize(shared_context)
                except Exception as e:
                    self.logs.append(f"[ERROR] [{code}] initialize 出错: {str(e)}")

        if not handlers:
            return self._error_result("所有股票策略初始化失败")

        # ---- 5. 主循环：按日期遍历 ----
        equity_curve = []
        self.logs.append("[INFO] 组合回测开始...")

        for date_idx, current_date in enumerate(common_dates):
            if self._cancelled:
                self.logs.append("[INFO] 回测已被用户取消")
                break

            date_str = current_date.strftime('%Y-%m-%d')
            shared_context.current_dt = current_date
            shared_context._pending_orders = []

            if progress_callback:
                try:
                    progress_callback(date_idx, len(common_dates))
                except Exception:
                    pass

            # 5a. 更新各股票最新价格
            for code, df in stock_dfs.items():
                if current_date in df.index:
                    try:
                        # 使用 .at 直接获取标量值，避免 Series 问题
                        close_val = df.at[current_date, 'close']
                        shared_context._last_prices[code] = float(close_val)
                    except (KeyError, ValueError, TypeError) as e:
                        self.logs.append(f"[WARN] [{code}] 获取 {current_date} 收盘价失败: {e}")
                        shared_context._last_prices[code] = 0.0

            # 5b. 先执行全局 daily 函数（如指数情绪更新，每个交易日只执行一次）
            for gfunc in getattr(shared_context, '_global_daily_funcs', []):
                try:
                    gfunc(shared_context)
                except Exception as e:
                    self.logs.append(f"[ERROR] {date_str} 全局 daily 函数出错: {str(e)}")

            # 5c. 对每只股票调用 handle_bar（收集订单，不执行）
            for code, handler in handlers.items():
                df = stock_dfs[code]
                if current_date not in df.index:
                    continue  # 该股票当日停牌，跳过

                # 定位该股票在当前日期的索引
                idx_pos = df.index.get_loc(current_date)
                if isinstance(idx_pos, int):
                    handler.current_idx = idx_pos
                elif isinstance(idx_pos, slice):
                    handler.current_idx = idx_pos.start
                elif hasattr(idx_pos, '__len__') and len(idx_pos) > 0:
                    handler.current_idx = idx_pos[0]
                else:
                    self.logs.append(f"[WARN] [{code}] 日期 {current_date} 在索引中未找到，跳过该日")
                    continue

                bar = df.iloc[handler.current_idx]
                bar_dict = {
                    'open': bar['open'],
                    'high': bar['high'],
                    'low': bar['low'],
                    'close': bar['close'],
                    'volume': bar.get('volume', 0),
                }

                # 临时设置 context.stock
                stock_code_pure = code.split('.')[0] if '.' in code else code
                shared_context.stock = stock_code_pure

                # 执行 per-stock run_daily 注册的函数（非指数情绪）
                for dfunc in handler.daily_functions:
                    try:
                        dfunc(shared_context)
                    except Exception as e:
                        self.logs.append(f"[ERROR] [{code}] {date_str} run_daily 出错: {str(e)}")

                try:
                    handler.handle_bar(shared_context, bar_dict)
                except Exception as e:
                    self.logs.append(f"[ERROR] [{code}] {date_str} handle_bar 出错: {str(e)}")

            # 5d. 执行订单：先卖出，再买入
            pending = shared_context._pending_orders
            sells = [o for o in pending if o['shares'] < 0]
            buys = [o for o in pending if o['shares'] > 0]

            # 先执行卖出（释放资金）
            for order in sells:
                self._execute_order(order, date_str, shared_context)

            # 再执行买入（检查资金）
            for order in buys:
                self._execute_order(order, date_str, shared_context)

            # 5d. 计算组合总资产
            cash = shared_context.portfolio['cash']
            holdings_value = 0.0
            for h_code, h_shares in shared_context.portfolio['holdings'].items():
                price = shared_context._last_prices.get(h_code, 0.0)
                # 确保 price 是标量
                if hasattr(price, 'item'):
                    price = price.item()
                elif isinstance(price, (pd.Series, np.ndarray)):
                    price = price[0] if len(price) > 0 else 0.0
                holdings_value += h_shares * float(price)
            total_assets = cash + holdings_value
            equity_curve.append({
                'date': date_str,
                'value': round(total_assets, 2),
            })

        self.logs.append("[INFO] 组合回测结束。")

        # ---- 6. 计算绩效指标 ----
        metrics = self._compute_metrics(equity_curve, initial_cash)

        # ---- 7. 计算股票绩效归因 ----
        stock_performance = self._compute_stock_performance()

        result = {
            'success': True,
            'signals': self.trade_signals,
            'equity_curve': equity_curve,
            'metrics': metrics,
            'logs': self.logs,
            'errors': [],
            'stock_performance': stock_performance,
        }

        # ---- 8. 基准对比处理 ----
        if benchmark_code and hasattr(self.data_source, 'get_benchmark_kline'):
            bm_df = self.data_source.get_benchmark_kline(benchmark_code, start_date, end_date)
            if not bm_df.empty and len(bm_df) >= 2:
                bm_df['trade_date'] = pd.to_datetime(bm_df['trade_date'])
                bm_df = bm_df.set_index('trade_date')
                bm_nav = bm_df['close'] / bm_df['close'].iloc[0]

                # --- 关键修改：按 equity_curve 的日期对齐基准曲线 ---
                eq_dates = pd.to_datetime([pt['date'] for pt in equity_curve])
                # 将 bm_nav 转换为 Series，索引为日期
                bm_nav_series = bm_nav  # 已经是 Series
                # 对齐：取 equity_curve 的日期，缺失值向前填充
                bm_nav_aligned = bm_nav_series.reindex(eq_dates, method='ffill')
                # 生成对齐后的基准曲线列表
                result['benchmark_equity_curve'] = [
                    {'date': d.strftime('%Y-%m-%d'), 'value': round(v, 4) if pd.notna(v) else None}
                    for d, v in zip(eq_dates, bm_nav_aligned)
                ]
                result['benchmark_code'] = benchmark_code

                # 指标计算仍使用原始 bm_df（与策略净值对齐）
                eq_df = pd.DataFrame(equity_curve)
                eq_df['date'] = pd.to_datetime(eq_df['date'])
                eq_df = eq_df.set_index('date')
                strategy_nav = eq_df['value'] / initial_cash
                bm_metrics = calculate_benchmark_metrics(strategy_nav, bm_df['close'])
                if bm_metrics:
                    metrics.update(bm_metrics)
                    result['metrics'] = metrics

                # 调试输出
                print("[DEBUG] benchmark_equity_curve sample:",
                      result['benchmark_equity_curve'][:3] if result.get('benchmark_equity_curve') else "None")
            else:
                self.logs.append(f"[WARN] 基准 {benchmark_code} 数据不足，无法对比")

        return result

    def _make_get_index_history(self, shared_context, start_date, end_date):
        """创建一个绑定了共享上下文和缓存的 get_index_history 函数。"""
        index_cache = self._index_cache

        def get_index_history(index_code, count, field, strict=True):
            if index_code not in index_cache:
                try:
                    if hasattr(self.data_source, 'get_benchmark_kline'):
                        bm_df = self.data_source.get_benchmark_kline(index_code, start_date, end_date)
                        if bm_df.empty:
                            index_cache[index_code] = None
                            return np.array([])
                        bm_df['trade_date'] = pd.to_datetime(bm_df['trade_date'])
                        bm_df = bm_df.set_index('trade_date')
                        if field == 'volume' and 'volume' not in bm_df.columns:
                            bm_df['volume'] = 0
                        index_cache[index_code] = bm_df
                    else:
                        index_cache[index_code] = None
                        return np.array([])
                except Exception as e:
                    self.logs.append(f"[WARN] 加载指数 {index_code} 数据失败: {str(e)}")
                    index_cache[index_code] = None
                    return np.array([])

            df = index_cache.get(index_code)
            if df is None:
                return np.array([])

            current_date = shared_context.current_dt
            if current_date is None:
                return np.array([])

            current_date = pd.Timestamp(current_date)
            date_was_adjusted = False
            if current_date not in df.index:
                available_dates = df.index[df.index <= current_date]
                if len(available_dates) == 0:
                    return np.array([])
                current_date = available_dates[-1]
                date_was_adjusted = True

            idx = df.index.get_loc(current_date)
            if isinstance(idx, slice):
                idx = idx.start
            elif hasattr(idx, '__len__') and len(idx) > 0:
                idx = idx[0]

            if strict and not date_was_adjusted:
                end = idx
            else:
                end = idx + 1

            start = max(0, end - count)
            if start >= end:
                return np.array([])

            sub_df = df.iloc[start:end]
            if field not in sub_df.columns:
                return np.array([])
            return sub_df[field].values.astype(float)

        return get_index_history

    def _execute_order(self, order, date_str, shared_context):
        """执行单个订单：更新现金和持仓，记录信号。扣除佣金和印花税。"""
        code = order['code']
        shares = order['shares']
        price = order['price']
        commission_rate = order.get('commission_rate', 0.0003)
        stamp_tax_rate = order.get('stamp_tax_rate', 0.001)
        portfolio = shared_context.portfolio
        cash = portfolio['cash']
        holdings = portfolio['holdings']

        trade_amount = abs(shares) * price
        commission = trade_amount * commission_rate
        stamp_tax = 0
        if shares < 0:
            stamp_tax = trade_amount * stamp_tax_rate

        if shares > 0:
            # 买入：检查资金（含佣金，买入不收印花税）
            total_cost = trade_amount + commission
            if total_cost > cash:
                self.logs.append(f"[WARN] [{code}] {date_str} 资金不足：需要 {total_cost:.2f}，现金 {cash:.2f}，跳过买入")
                return
            cash -= total_cost
        else:
            # 卖出：检查持仓，收入减去佣金和印花税
            current_shares = holdings.get(code, 0)
            sell_shares = abs(shares)
            if current_shares < sell_shares:
                self.logs.append(f"[WARN] [{code}] {date_str} 持仓不足：需要 {sell_shares}，持有 {current_shares}，调整为目标可卖数量")
                shares = -current_shares
                if shares == 0:
                    return
                trade_amount = abs(shares) * price
                commission = trade_amount * commission_rate
                stamp_tax = trade_amount * stamp_tax_rate
            cash += (trade_amount - commission - stamp_tax)

        portfolio['cash'] = cash

        # 更新持仓
        current = holdings.get(code, 0)
        new_shares = current + shares
        if abs(new_shares) < 1e-8:
            if code in holdings:
                del holdings[code]
        else:
            holdings[code] = new_shares

        # 记录信号
        trade_type = 'buy' if shares > 0 else 'sell'
        self.trade_signals.append({
            'date': date_str,
            'code': code,
            'type': trade_type,
            'price': round(price, 2),
            'shares': round(abs(shares), 2),
            'reason': order.get('reason', ''),
        })

    def _compute_metrics(self, equity_curve, initial_cash):
        """计算组合绩效指标，与 BacktestExecutor._compute_metrics 逻辑一致。"""
        if not equity_curve:
            return {}
        final_value = equity_curve[-1]['value']
        total_ret = (final_value / initial_cash - 1) * 100.0

        returns = []
        for i in range(1, len(equity_curve)):
            prev = equity_curve[i - 1]['value']
            cur = equity_curve[i]['value']
            if prev > 0:
                returns.append((cur - prev) / prev)
        if not returns:
            return {'total_return': round(total_ret, 2), 'total_trades': len(self.trade_signals)}

        total_return = round(total_ret, 2)
        n_days = len(returns)
        if n_days > 0:
            annual_ret = ((1 + total_ret / 100.0) ** (250.0 / n_days) - 1) * 100.0
        else:
            annual_ret = 0.0

        # 最大回撤
        peak = initial_cash
        max_drawdown = 0.0
        max_drawdown_start = 0
        max_drawdown_end = 0
        drawdown_duration = 0
        current_peak_idx = 0
        for idx, pt in enumerate(equity_curve):
            if pt['value'] > peak:
                peak = pt['value']
                current_peak_idx = idx
            dd = (peak - pt['value']) / peak * 100.0
            if dd > max_drawdown:
                max_drawdown = dd
                max_drawdown_start = current_peak_idx
                max_drawdown_end = idx
        if max_drawdown_end > max_drawdown_start:
            drawdown_duration = max_drawdown_end - max_drawdown_start

        # 夏普比率
        if len(returns) > 0:
            mean_ret = np.mean(returns)
            std_ret = np.std(returns, ddof=1)
            sharpe = (mean_ret / std_ret) * np.sqrt(250.0) if std_ret > 0 else 0.0
        else:
            sharpe = 0.0

        # 年化波动率
        if len(returns) > 0:
            annual_vol = np.std(returns, ddof=1) * np.sqrt(250.0) * 100.0
        else:
            annual_vol = 0.0

        # ---------- 新增：计算胜率（基于买卖配对）----------
        # 按股票分组，使用 FIFO 队列配对
        from collections import defaultdict
        buy_queues = defaultdict(list)  # key: code, value: list of {'price': price, 'shares': shares}
        win_trades = 0
        total_trades = 0

        for sig in self.trade_signals:
            code = sig['code']
            if sig['type'] == 'buy':
                buy_queues[code].append({'price': sig['price'], 'shares': sig['shares']})
            elif sig['type'] == 'sell':
                sell_price = sig['price']
                sell_shares = sig['shares']
                queue = buy_queues.get(code, [])
                while sell_shares > 1e-8 and queue:
                    buy = queue[0]
                    matched = min(buy['shares'], sell_shares)
                    profit = (sell_price - buy['price']) * matched
                    if profit > 0:
                        win_trades += 1
                    total_trades += 1
                    buy['shares'] -= matched
                    sell_shares -= matched
                    if buy['shares'] < 1e-8:
                        queue.pop(0)
        win_rate = round(win_trades / total_trades * 100, 2) if total_trades > 0 else 0.0

        # ---------- 信息比率（使用年化收益率/年化波动率，无风险利率=0）----------
        if annual_vol > 0:
            information_ratio = round(annual_ret / annual_vol, 2)
        else:
            information_ratio = 0.0

        metrics = {
            'total_return': round(total_return, 2),
            'annual_return': round(annual_ret, 2),
            'max_drawdown': round(max_drawdown, 2),
            'max_drawdown_duration': drawdown_duration,
            'sharpe_ratio': round(sharpe, 2),
            'annual_volatility': round(annual_vol, 2),
            'information_ratio': information_ratio,
            'win_rate': win_rate,
            'total_trades': total_trades
        }
        return metrics

    def _compute_stock_performance(self):
        """按股票分组计算绩效归因数据。
        使用先进先出配对逻辑：每笔卖出按顺序与最早未配对的买入配对。
        """
        from collections import defaultdict

        stock_signals = defaultdict(list)
        for sig in self.trade_signals:
            stock_signals[sig['code']].append(sig)

        result = []
        for code, signals in stock_signals.items():
            signals.sort(key=lambda x: x['date'])

            buy_queue = []          # [{price, shares}]
            completed_trades = []   # [profit]

            for sig in signals:
                if sig['type'] == 'buy':
                    buy_queue.append({'price': sig['price'], 'shares': sig['shares']})
                elif sig['type'] == 'sell':
                    sell_price = sig['price']
                    sell_remaining = sig['shares']

                    while sell_remaining > 1e-8 and buy_queue:
                        buy = buy_queue[0]
                        matched = min(buy['shares'], sell_remaining)
                        profit = (sell_price - buy['price']) * matched
                        completed_trades.append(profit)

                        buy['shares'] -= matched
                        sell_remaining -= matched

                        if buy['shares'] < 1e-8:
                            buy_queue.pop(0)

            total_trades = len(completed_trades)
            win_trades = sum(1 for p in completed_trades if p > 0)
            total_profit = round(sum(completed_trades), 2)
            win_rate = round(win_trades / total_trades * 100, 1) if total_trades > 0 else 0.0
            avg_profit = round(total_profit / total_trades, 2) if total_trades > 0 else 0.0

            name = code
            try:
                db = getattr(self.data_source, 'db', None)
                if db is not None:
                    fetched = db.get_name_by_code(code)
                    if fetched:
                        name = fetched
            except Exception:
                pass

            result.append({
                'code': code,
                'name': name,
                'total_trades': total_trades,
                'win_trades': win_trades,
                'total_profit': total_profit,
                'win_rate': win_rate,
                'avg_profit': avg_profit,
            })

        result.sort(key=lambda x: x['total_profit'], reverse=True)

        return result

    def _error_result(self, msg):
        return {
            'success': False,
            'error': msg,
            'signals': [],
            'equity_curve': [],
            'metrics': {},
            'logs': self.logs + [f"[ERROR] {msg}"],
            'errors': [msg],
        }
