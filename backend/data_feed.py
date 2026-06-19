import json
import datetime
import bisect
import pandas as pd
from backend.db import Database
import math

class DataFeed:
    _kline_cache = {}   # key: 纯代码(不带后缀)，value: {"dates": [...], "values": [[o,c,l,h], ...]}

    def __init__(self):
        self.db = Database()

    def _format_date(self, d):
        """统一日期格式为 'YYYY-MM-DD' 字符串"""
        if isinstance(d, (pd.Timestamp, datetime.datetime)):
            return d.strftime('%Y-%m-%d')
        s = str(d).strip()
        # 8 位数字格式: 20260101 → 2026-01-01
        if len(s) == 8 and s.isdigit():
            return f"{s[:4]}-{s[4:6]}-{s[6:]}"
        # 已经是 YYYY-MM-DD 格式
        if len(s) == 10 and s[4] == '-' and s[7] == '-':
            return s
        # 处理 '2026-01-05 00:00:00' 之类的格式
        if ' ' in s:
            return s[:10]
        # 兜底
        return s

    def _slice_by_date_range(self, cached, start, end):
        """
        在已排序的日期数组上，用二分查找定位 [start, end] 区间，避免全量表遍历。
        返回 (dates_sub, values_sub)
        """
        dates = cached["dates"]
        values = cached["values"]
        if not dates:
            return [], []
        lo = bisect.bisect_left(dates, start)
        hi = bisect.bisect_right(dates, end)
        return dates[lo:hi], values[lo:hi]

    def _aggregate_to_period(self, df, period):
        """将日线 DataFrame 聚合成周线或月线"""
        if period == 'daily':
            return df
        df_copy = df.copy()
        agg_dict = {
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }
        has_turnover = 'turnover_rate_f' in df_copy.columns
        if has_turnover:
            agg_dict['turnover_rate_f'] = 'mean'
        if period == 'weekly':
            df_copy['_group'] = df_copy.index.to_period('W').start_time
        elif period == 'monthly':
            df_copy['_group'] = df_copy.index.to_period('M').start_time
        else:
            return df
        result = df_copy.groupby('_group').agg(agg_dict)
        result.index = pd.to_datetime(result.index)
        # 重新排列列顺序以匹配缓存格式: open, close, low, high, volume, [turnover_rate_f]
        cols = ['open', 'close', 'low', 'high', 'volume']
        if has_turnover:
            cols.append('turnover_rate_f')
        result = result[cols]
        # 确保数据类型正确
        for col in ['open', 'close', 'low', 'high']:
            result[col] = result[col].astype(float).round(2)
        result['volume'] = result['volume'].astype(int)
        if has_turnover:
            result['turnover_rate_f'] = result['turnover_rate_f'].astype(float).round(2)
        return result

    def get_kline_json(self, code, start_date=None, end_date=None, limit=0, period='daily'):
        """获取K线数据 JSON，支持缓存，根据日期范围过滤，并支持限制行数"""
        code_pure = code.split('.')[0]

        def safe_float(x):
            try:
                if x is None or (isinstance(x, float) and math.isnan(x)):
                    return 0.0
                return round(float(x), 2)
            except (ValueError, TypeError):
                return 0.0

        def safe_int(x):
            try:
                if x is None or (isinstance(x, float) and math.isnan(x)):
                    return 0
                return int(float(x))
            except (ValueError, TypeError):
                return 0

        # 如果缓存中没有，则从数据库加载全量数据
        if code_pure not in self._kline_cache:
            df = self.db.get_kline(code, start_date=None, end_date=None, limit=0)  # 全量
            if df is None or df.empty:
                self._kline_cache[code_pure] = None
                return json.dumps({"error": "无数据"})

            # 将 DataFrame 转换为缓存格式
            dates = [self._format_date(d) for d in df['trade_date']]
            has_turnover = 'turnover_rate_f' in df.columns
            if has_turnover:
                values = [[safe_float(o), safe_float(c), safe_float(l), safe_float(h), safe_int(v), safe_float(t)]
                          for o, c, l, h, v, t in zip(df['open'], df['close'], df['low'], df['high'], df['volume'], df['turnover_rate_f'])]
            else:
                values = [[safe_float(o), safe_float(c), safe_float(l), safe_float(h), safe_int(v)]
                          for o, c, l, h, v in zip(df['open'], df['close'], df['low'], df['high'], df['volume'])]
            self._kline_cache[code_pure] = {"dates": dates, "values": values}

        cached = self._kline_cache.get(code_pure)
        if cached is None:
            return json.dumps({"error": "无数据"})

        # 使用二分查找进行日期范围过滤
        if start_date is None and end_date is None:
            filtered_dates = cached["dates"]
            filtered_values = cached["values"]
        else:
            if start_date is None:
                start_date = "2010-01-01"
            if end_date is None:
                end_date = "2026-12-31"
            filtered_dates, filtered_values = self._slice_by_date_range(cached, start_date, end_date)

        # 聚合到目标周期（周线/月线）
        if period != 'daily' and len(filtered_dates) > 0:
            cols = ['open', 'close', 'low', 'high', 'volume']
            has_turnover = any(len(v) >= 6 for v in filtered_values)
            if has_turnover:
                cols.append('turnover_rate_f')
            df = pd.DataFrame(filtered_values, columns=cols)
            df.index = pd.to_datetime(filtered_dates)
            df_agg = self._aggregate_to_period(df, period)
            filtered_dates = [d.strftime('%Y-%m-%d') for d in df_agg.index]
            filtered_values = []
            for _, row in df_agg.iterrows():
                vals = [
                    float(row['open']),
                    float(row['close']),
                    float(row['low']),
                    float(row['high']),
                    int(row['volume'])
                ]
                if has_turnover and 'turnover_rate_f' in df_agg.columns:
                    vals.append(float(row['turnover_rate_f']))
                filtered_values.append(vals)

        # 如果 limit > 0，取尾部 limit 条
        if limit > 0 and len(filtered_dates) > limit:
            filtered_dates = filtered_dates[-limit:]
            filtered_values = filtered_values[-limit:]

        result = {"dates": filtered_dates, "values": filtered_values}
        return json.dumps(result)

    def get_latest_price(self, code):
        """返回最新价、日期、前一收盘价及涨跌幅"""
        code_pure = code.split('.')[0]
        # 若缓存缺失则利用 get_kline_json 加载全量数据
        if code_pure not in self._kline_cache:
            self.get_kline_json(code)  # 加载并缓存
        cached = self._kline_cache.get(code_pure)
        if cached is None:
            return {"error": "无数据"}
        dates = cached["dates"]
        values = cached["values"]
        if len(dates) < 2:
            return {"error": "数据不足两个交易日"}
        last_date = dates[-1]
        last_close = values[-1][1]      # close index 1
        prev_close = values[-2][1]
        price = last_close
        change = round(last_close - prev_close, 2)
        change_pct = round((change / prev_close) * 100, 2) if prev_close != 0 else 0.0
        return {
            "price": price,
            "date": last_date,
            "prev_close": prev_close,
            "change": change,
            "change_pct": change_pct
        }

    def get_realtime_price(self, code):
        """从腾讯财经 HTTP 接口获取实时行情。

        :param code: 纯数字股票代码（如 '000001'），自动转换为 sh/sz 前缀
        :return: dict 或 None（失败时）
            字段: price, prev_close, change_pct, high, low, volume(手), amount(万元)
        """
        from urllib import request
        import re

        code_pure = code.split('.')[0]
        if code_pure.startswith(('60', '68')):
            q_code = f'sh{code_pure}'
        else:
            q_code = f'sz{code_pure}'

        url = f'https://web.sqt.gtimg.cn/q={q_code}'
        try:
            req = request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with request.urlopen(req, timeout=3) as resp:
                raw = resp.read()
            text = raw.decode('gbk', errors='replace')

            # 解析 ~ 分隔的字段: v_shXXXXXX="1~名称~代码~最新价~昨收~..."
            m = re.search(r'="(.+)"', text)
            if not m:
                return None
            fields = m.group(1).split('~')
            if len(fields) < 38:
                return None

            def _f(i):
                try:
                    return float(fields[i]) if fields[i] else 0.0
                except ValueError:
                    return 0.0

            def _i(i):
                try:
                    return int(float(fields[i])) if fields[i] else 0
                except ValueError:
                    return 0

            price = _f(3)
            prev_close = _f(4)
            if price <= 0 or prev_close <= 0:
                return None

            return {
                'price': price,
                'prev_close': prev_close,
                'open': _f(5),           # 今开
                'change_pct': _f(32),
                'high': _f(33),
                'low': _f(34),
                'volume': _i(36),        # 手
                'amount': _f(37),        # 万元
            }
        except Exception:
            return None

    def get_realtime_quotes_batch(self, codes, fields=None):
        """批量获取实时行情（腾讯批量接口，最多 50 只/次）。

        :param codes: 纯数字代码列表，如 ['000001', '600519']
        :param fields: 需要的字段名列表，默认 ['change_pct']
            可选: price, prev_close, open, change_pct, high, low, volume, amount
        :return: dict {code: {field: value, ...}}，获取失败的代码不在结果中
        """
        from urllib import request
        import re

        if not codes:
            return {}

        if fields is None:
            fields = ['change_pct']

        FIELD_MAP = {
            'price': 3, 'prev_close': 4, 'open': 5,
            'change_pct': 32, 'high': 33, 'low': 34,
            'volume': 36, 'amount': 37,
        }

        wanted_indices = [(f, FIELD_MAP[f]) for f in fields if f in FIELD_MAP]
        if not wanted_indices:
            return {}

        result = {}

        def _q_code(c):
            c = c.split('.')[0]
            if c.startswith(('60', '68')):
                return f'sh{c}'
            return f'sz{c}'

        # 腾讯接口单次最多 ~50 只
        batch_size = 50
        for i in range(0, len(codes), batch_size):
            batch = codes[i:i + batch_size]
            q_codes = [f'{_q_code(c)}' for c in batch]
            url = f'https://web.sqt.gtimg.cn/q={",".join(q_codes)}'

            try:
                req = request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with request.urlopen(req, timeout=5) as resp:
                    raw = resp.read()
                text = raw.decode('gbk', errors='replace')

                # 按行匹配: v_sz000001="1~平安银行~000001~...~0.50~...";
                for line in text.split('\n'):
                    m = re.search(r'v_s([hz])', line)
                    if not m:
                        continue
                    exchange = m.group(1)
                    content_m = re.search(r'="(.+)"', line)
                    if not content_m:
                        continue
                    parts = content_m.group(1).split('~')
                    if len(parts) < 38:
                        continue

                    # parts[2] 是纯数字代码
                    pure_code = parts[2] if len(parts) > 2 else None
                    if not pure_code or not pure_code.isdigit():
                        continue

                    entry = {}
                    valid = True
                    for fname, idx in wanted_indices:
                        try:
                            entry[fname] = float(parts[idx]) if parts[idx] else 0.0
                        except ValueError:
                            entry[fname] = 0.0
                            valid = False

                    if valid:
                        result[pure_code] = entry

            except Exception as e:
                # 单批失败不中断整体，继续下一批
                continue

        return result

    def _mock_kline_json(self, code):
        """生成覆盖 2010-01-01 至 2026-12-31 的模拟K线JSON（周线，数据量可控）"""
        import numpy as np
        np.random.seed(42)
        dates_all = pd.date_range("2010-01-01", "2026-12-31", freq='W')
        n = len(dates_all)
        opens = 12.0 + np.cumsum(np.random.randn(n) * 0.5)
        closes = opens + np.random.randn(n) * 0.6
        highs = np.maximum(opens, closes) + np.random.rand(n) * 0.5
        lows = np.minimum(opens, closes) - np.random.rand(n) * 0.5

        date_strs = [d.strftime('%Y-%m-%d') for d in dates_all]
        volumes = np.random.randint(100000, 500000, n)
        values = [[round(opens[i],2), round(closes[i],2), round(lows[i],2), round(highs[i],2), int(volumes[i])] for i in range(n)]
        return json.dumps({"dates": date_strs, "values": values})

    def get_close_price_on_date(self, code, target_date):
        """从缓存中获取指定日期（或最近的前一日）的收盘价。

        :param code: 股票纯数字代码
        :param target_date: 'YYYY-MM-DD' 字符串
        :return: float 或 None
        """
        code_pure = code.split('.')[0]
        if code_pure not in self._kline_cache:
            self.get_kline_json(code)
        cached = self._kline_cache.get(code_pure)
        if cached is None:
            return None
        dates = cached["dates"]
        values = cached["values"]
        if not dates:
            return None
        try:
            idx = dates.index(target_date)
            return values[idx][1]
        except ValueError:
            lo = bisect.bisect_left(dates, target_date)
            if lo > 0 and lo <= len(values):
                return values[lo - 1][1]
            return values[0][1] if values else None

    def get_prev_close(self, code, target_date=None):
        """从 K 线缓存中获取指定日期（或最近日期）的前一交易日收盘价。

        :param code: 股票纯数字代码
        :param target_date: 'YYYY-MM-DD' 字符串，若为 None 则取最新
        :return: float 或 None
        """
        code_pure = code.split('.')[0]
        if code_pure not in self._kline_cache:
            self.get_kline_json(code)
        cached = self._kline_cache.get(code_pure)
        if cached is None:
            return None
        dates = cached["dates"]
        values = cached["values"]
        if len(dates) < 2:
            return None
        if target_date is None:
            return values[-2][1]  # close is index 1
        try:
            idx = dates.index(target_date)
        except ValueError:
            idx = bisect.bisect_left(dates, target_date)
        if idx <= 0 or idx >= len(values):
            return None
        return values[idx - 1][1]

    def get_benchmark_kline(self, benchmark_code, start_date=None, end_date=None):
        """获取基准指数的日线收盘价序列。

        :param benchmark_code: 指数 ts_code，如 '000300.SH'
        :param start_date: 'YYYY-MM-DD' 或 None（不限制）
        :param end_date: 'YYYY-MM-DD' 或 None（不限制）
        :return: DataFrame，包含 trade_date 和 close 列，按日期升序
        """
        try:
            from sqlalchemy import text
            params = {"code": benchmark_code}
            sql = "SELECT trade_date, close FROM index_daily WHERE ts_code = :code"
            if start_date:
                sql += " AND trade_date >= :start"
                params["start"] = start_date
            if end_date:
                sql += " AND trade_date <= :end"
                params["end"] = end_date
            sql += " ORDER BY trade_date ASC"
            with self.db.engine.connect() as conn:
                df = pd.read_sql(text(sql), conn, params=params)
            if df.empty:
                print(f"[DataFeed] 基准 {benchmark_code} 无数据")
                return pd.DataFrame(columns=['trade_date', 'close'])
            return df
        except Exception as e:
            print(f"[DataFeed] 获取基准数据失败 ({benchmark_code}): {e}")
            return pd.DataFrame(columns=['trade_date', 'close'])
