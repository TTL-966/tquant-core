import os
import sys
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError


def get_db_path():
    """返回数据库文件路径，兼容开发环境和 PyInstaller 打包环境。
    打包后：exe 同级目录下的 tquant.db
    开发环境：项目根目录下的 tquant.db
    """
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, 'tquant.db')


class Database:
    def __init__(self):
        self.db_path = get_db_path()
        if not os.path.exists(self.db_path):
            err_msg = (
                f"数据库文件未找到:\n\n"
                f"   {self.db_path}\n\n"
                f"请将 tquant.db 文件放在程序所在目录。\n"
                f"如果数据库文件名不同，请重命名为 tquant.db。"
            )
            print(f"[ERROR] {err_msg}")
            # 尝试弹出 GUI 提示（如果有 QMessageBox 可用）
            try:
                from PySide6.QtWidgets import QMessageBox, QApplication
                app = QApplication.instance()
                if app:
                    QMessageBox.critical(None, "数据库未找到", err_msg)
                else:
                    QMessageBox.critical(None, "数据库未找到", err_msg)
            except Exception:
                pass
            raise FileNotFoundError(err_msg)

        self.engine = create_engine(f'sqlite:///{self.db_path}?check_same_thread=False', echo=False)
        # WAL 模式提升并发读性能
        with self.engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.commit()
        self._init_tables()

    def _init_tables(self):
        with self.engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS stock_daily_qfq_with_name (
                    ts_code TEXT,
                    name TEXT,
                    trade_date TEXT,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    vol INTEGER,
                    amount REAL,
                    PRIMARY KEY (ts_code, trade_date)
                )
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_ts_code_trade_date
                ON stock_daily_qfq_with_name(ts_code, trade_date)
            """))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS stock_basic (
                    code TEXT PRIMARY KEY,
                    name TEXT
                )
            """))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS stock_industry (
                    ts_code TEXT PRIMARY KEY,
                    stock_name TEXT,
                    industry TEXT,
                    industry_classification TEXT
                )
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_stock_industry_industry
                ON stock_industry(industry)
            """))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS index_components (
                    index_code TEXT,
                    stock_code TEXT,
                    update_date TEXT,
                    PRIMARY KEY (index_code, stock_code)
                )
            """))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS stock_financial (
                    ts_code TEXT PRIMARY KEY,
                    pe_ttm REAL,
                    pb REAL,
                    roe REAL,
                    total_mv REAL,
                    revenue REAL,
                    net_profit REAL,
                    update_date TEXT
                )
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_financial_pe ON stock_financial(pe_ttm)
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_financial_pb ON stock_financial(pb)
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_financial_roe ON stock_financial(roe)
            """))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS stock_industry_detail (
                    ts_code TEXT PRIMARY KEY,
                    stock_name TEXT,
                    industry_level1 TEXT,
                    industry_level2 TEXT,
                    industry_level3 TEXT,
                    concept_sectors TEXT,
                    update_date TEXT
                )
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_industry_l1 ON stock_industry_detail(industry_level1)
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_industry_l2 ON stock_industry_detail(industry_level2)
            """))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS stock_financial_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_code TEXT NOT NULL,
                    report_date TEXT NOT NULL,
                    pe_ttm REAL,
                    pb REAL,
                    roe REAL,
                    total_mv REAL,
                    revenue REAL,
                    net_profit REAL,
                    update_date TEXT,
                    UNIQUE(ts_code, report_date)
                )
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_history_ts_date
                ON stock_financial_history(ts_code, report_date)
            """))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS concept (
                    concept_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    concept_name TEXT UNIQUE NOT NULL
                )
            """))
            # 股票概念关联表
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS stock_concept (
                    ts_code TEXT NOT NULL,
                    concept_id INTEGER NOT NULL,
                    PRIMARY KEY (ts_code, concept_id),
                    FOREIGN KEY (concept_id) REFERENCES concept(concept_id)
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_stock_concept_code ON stock_concept(ts_code)"))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS backtest_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_name TEXT,
                    stock_pool TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    initial_cash REAL,
                    metrics TEXT,
                    signals TEXT,
                    equity_curve TEXT,
                    stock_performance TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS fund_flow_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    main_net REAL,
                    super_net REAL,
                    big_net REAL,
                    medium_net REAL,
                    small_net REAL,
                    update_time TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(ts_code, trade_date)
                )
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_ff_ts_date
                ON fund_flow_history(ts_code, trade_date)
            """))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS auto_trade_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    stock_code TEXT NOT NULL,
                    action TEXT NOT NULL,
                    price REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    mode TEXT,
                    order_id TEXT
                )
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_auto_trade_log_time
                ON auto_trade_log(timestamp)
            """))

            conn.commit()

    def _get_stock_suffix(self, code):
        code = str(code).zfill(6)
        if code.startswith(('000', '001', '002', '003', '300', '301')):
            return '.SZ'
        if code.startswith(('600', '601', '603', '605', '688', '689')):
            return '.SH'
        if code.startswith('8'):
            return '.BJ'
        return '.SZ'

    def _query_kline(self, code, start_date, end_date, limit=0):
        if start_date is None:
            start_date = "2010-01-01"
        if end_date is None:
            end_date = "2026-12-31"

        def do_query():
            if limit > 0:
                sql = text("""
                    SELECT trade_date, open, high, low, close, vol AS volume, turnover_rate_f
                    FROM stock_daily_qfq_with_name
                    WHERE ts_code = :code
                      AND trade_date >= :start
                      AND trade_date <= :end
                    ORDER BY trade_date DESC LIMIT :limit
                """)
                with self.engine.connect() as conn:
                    df = pd.read_sql(
                        sql,
                        conn,
                        params={"code": code, "start": start_date, "end": end_date, "limit": limit}
                    )
                if not df.empty:
                    df = df.sort_values('trade_date', ascending=True).reset_index(drop=True)
                return df
            else:
                sql = text("""
                    SELECT trade_date, open, high, low, close, vol AS volume, turnover_rate_f
                    FROM stock_daily_qfq_with_name
                    WHERE ts_code = :code
                      AND trade_date >= :start
                      AND trade_date <= :end
                    ORDER BY trade_date ASC
                """)
                with self.engine.connect() as conn:
                    df = pd.read_sql(sql, conn, params={"code": code, "start": start_date, "end": end_date})
                return df

        try:
            return do_query()
        except OperationalError:
            self.engine.dispose()
            return do_query()

    def get_kline(self, code, start_date="2010-01-01", end_date="2026-12-31", limit=0):
        if start_date is None:
            start_date = "2010-01-01"
        if end_date is None:
            end_date = "2026-12-31"
        original_code = code
        if '.' not in code:
            suffix = self._get_stock_suffix(code)
            code_for_query = f"{code}{suffix}"
        else:
            code_for_query = code
            suffix = '.' + code.split('.')[1]
        try:
            df = self._query_kline(code_for_query, start_date, end_date, limit)
            if not df.empty:
                return df
        except Exception as e:
            print("查询失败:", e)
        if '.' not in original_code:
            alt_suffix = None
            if suffix == '.SZ':
                alt_suffix = '.SH'
            elif suffix == '.SH':
                alt_suffix = '.SZ'
            if alt_suffix:
                alt_code = f"{original_code}{alt_suffix}"
                try:
                    df = self._query_kline(alt_code, start_date, end_date, limit)
                    if not df.empty:
                        print(f"[DB] 查询成功，返回 {len(df)} 条数据")
                        return df
                except Exception as e:
                    print("备用查询失败:", e)
        return self._generate_mock_data()

    def get_index_kline(self, ts_code, start_date="2010-01-01", end_date="2026-12-31"):
        if start_date is None:
            start_date = "2010-01-01"
        if end_date is None:
            end_date = "2026-12-31"

        def do_query():
            sql = text("""
                SELECT trade_date, open, high, low, close, vol AS volume, amount
                FROM index_daily
                WHERE ts_code = :code
                  AND trade_date >= :start
                  AND trade_date <= :end
                ORDER BY trade_date ASC
            """)
            with self.engine.connect() as conn:
                df = pd.read_sql(sql, conn, params={"code": ts_code, "start": start_date, "end": end_date})
            return df

        try:
            return do_query()
        except OperationalError:
            self.engine.dispose()
            return do_query()

    def _generate_mock_data(self):
        n_dates = pd.date_range("2010-01-01", "2026-12-31", freq='B')
        n = len(n_dates)
        np.random.seed(42)
        dates = n_dates
        base = 12.0
        opens = base + np.cumsum(np.random.randn(n) * 0.5)
        closes = opens + np.random.randn(n) * 0.6
        highs = np.maximum(opens, closes) + np.random.rand(n) * 0.5
        lows = np.minimum(opens, closes) - np.random.rand(n) * 0.5
        volumes = np.random.randint(100000, 500000, n)
        df = pd.DataFrame({
            'trade_date': [d.strftime('%Y%m%d') for d in dates],
            'open': opens,
            'high': highs,
            'low': lows,
            'close': closes,
            'volume': volumes
        })
        return df

    def connection_status(self):
        if not os.path.exists(self.db_path):
            return {"connected": False, "message": f"数据库文件不存在: {self.db_path}"}
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return {"connected": True, "message": f"SQLite 数据库连接正常 ({self.db_path})"}
        except Exception as e:
            return {"connected": False, "message": f"数据库连接异常: {str(e)}"}

    def search_stock(self, keyword):
        if not keyword:
            return []
        like = f"%{keyword}%"
        sql = text("""
            SELECT code, name FROM stock_basic
            WHERE code LIKE :like OR name LIKE :like
            LIMIT 50
        """)
        def do_query():
            with self.engine.connect() as conn:
                rows = conn.execute(sql, {"like": like}).fetchall()
            result = []
            for row in rows:
                code = row[0]
                name = row[1]
                result.append({"code": code, "name": name})
            return result
        try:
            return do_query()
        except OperationalError:
            self.engine.dispose()
            return do_query()

    def get_name_by_code(self, code):
        for suffix in ('.SZ', '.SH', '.BJ'):
            ts_code_candidate = f"{code}{suffix}"
            sql = text("""
                SELECT name FROM stock_daily_qfq_with_name
                WHERE ts_code = :ts_code
                LIMIT 1
            """)
            try:
                with self.engine.connect() as conn:
                    rows = conn.execute(sql, {"ts_code": ts_code_candidate}).fetchall()
                if rows:
                    name = rows[0][0]
                    return f"{name} ({code})"
            except Exception:
                continue
        like = f"{code}%"
        sql = text("""
            SELECT name FROM stock_daily_qfq_with_name
            WHERE ts_code LIKE :like
            LIMIT 1
        """)
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(sql, {"like": like}).fetchall()
            if rows:
                name = rows[0][0]
                return f"{name} ({code})"
        except Exception:
            pass
        return code

    def get_stock_status(self, code):
        """返回默认值。当前 stock_basic 表仅有 code/name 字段，无 list_date/delist_date。"""
        return {'listed': '1900-01-01', 'delisted': None}

    def get_industry_by_code(self, code):
        suffix = self._get_stock_suffix(code)
        ts_code = f"{code}{suffix}"
        sql = text("SELECT industry FROM stock_industry WHERE ts_code = :ts_code LIMIT 1")
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(sql, {"ts_code": ts_code}).fetchall()
            if rows:
                return rows[0][0]
            return None
        except Exception:
            return None

    def get_stocks_by_industry(self, industry_name):
        like = f"%{industry_name}%"
        sql = text("SELECT ts_code, stock_name FROM stock_industry WHERE industry LIKE :industry LIMIT 20")
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(sql, {"industry": like}).fetchall()
            result = []
            for row in rows:
                ts_code = row[0]
                name = row[1]
                pure_code = ts_code.split('.')[0] if '.' in ts_code else ts_code
                result.append({"code": pure_code, "name": name})
            return result
        except Exception:
            return []

    def get_index_stocks(self, index_code):
        try:
            sql = text(
                "SELECT stock_code FROM index_components "
                "WHERE index_code = :index_code ORDER BY stock_code"
            )
            with self.engine.connect() as conn:
                rows = conn.execute(sql, {"index_code": index_code}).fetchall()
            if rows:
                return [row[0].split('.')[0] if '.' in str(row[0]) else str(row[0]) for row in rows]
        except Exception:
            pass

        mock_indices = {
            '000300.XSHG': [
                '000001', '000002', '000063', '000333', '000651', '000725', '000858',
                '002142', '002415', '002594', '300750', '600000', '600009', '600016',
                '600028', '600030', '600036', '600048', '600050', '600104', '600276',
                '600309', '600519', '600585', '600809', '600887', '601012', '601088',
                '601166', '601288', '601318', '601328', '601398', '601668', '601857',
                '601888', '601939', '603259', '603288'
            ],
            '000905.XSHG': [
                '000012', '000021', '000039', '000050', '000060', '000066', '000100',
                '000155', '002013', '002028', '002049', '002074', '002091', '002110',
                '002129', '002138', '002155', '300001', '300003', '300014', '300024',
                '300033', '300037', '300058', '300070', '300088', '600004', '600008',
                '600012', '600017', '600018', '600019', '600020', '600021', '600022',
                '601000', '601001', '601003', '601005', '601006', '601008'
            ],
            '000852.XSHG': [
                '000158', '000301', '000401', '000420', '000426', '000501', '000510',
                '000519', '002001', '002003', '002007', '002008', '002010', '002011',
                '002017', '002019', '002020', '300002', '300004', '300005', '300006',
                '300007', '300008', '300009', '300010', '300011', '600001', '600002',
                '600003', '600005', '600006', '600007', '600010', '600011', '600012'
            ],
            '399006.XSHE': [
                '300001', '300003', '300014', '300015', '300024', '300033', '300037',
                '300058', '300059', '300070', '300088', '300122', '300124', '300142',
                '300146', '300207', '300251', '300274', '300316', '300347', '300408',
                '300413', '300433', '300450', '300498', '300502', '300529', '300558',
                '300595', '300601', '300628', '300661', '300750', '300759', '300760'
            ],
            '000688.XSHG': [
                '688001', '688005', '688008', '688009', '688012', '688036', '688065',
                '688111', '688126', '688187', '688223', '688256', '688303', '688396',
                '688516', '688536', '688561', '688599', '688728', '688777', '688981'
            ],
        }
        return mock_indices.get(index_code, [])

    def save_fund_flow_batch(self, records):
        """批量插入或替换资金流向记录。

        Args:
            records: list[dict], 每条包含 ts_code, trade_date, main_net,
                     super_net, big_net, medium_net, small_net
        """
        if not records:
            return
        sql = text("""
            INSERT OR REPLACE INTO fund_flow_history
                (ts_code, trade_date, main_net, super_net, big_net, medium_net, small_net)
            VALUES (:ts_code, :trade_date, :main_net, :super_net, :big_net, :medium_net, :small_net)
        """)
        try:
            with self.engine.begin() as conn:
                for r in records:
                    conn.execute(sql, {
                        "ts_code": r["ts_code"],
                        "trade_date": r["trade_date"],
                        "main_net": r.get("main_net"),
                        "super_net": r.get("super_net"),
                        "big_net": r.get("big_net"),
                        "medium_net": r.get("medium_net"),
                        "small_net": r.get("small_net"),
                    })
        except Exception as e:
            print(f"[DB] 保存资金流向失败: {e}")

    def get_fund_flow_history(self, code, start_date=None, end_date=None, limit=30):
        """查询某只股票的历史资金流向（按日期升序）。

        Args:
            code: 纯数字代码（如 '000001'）
            start_date: 起始日期 'YYYY-MM-DD'，默认不限
            end_date: 结束日期 'YYYY-MM-DD'，默认不限
            limit: 最大返回条数

        Returns:
            list[dict]: 按 trade_date 升序排列的历史记录
        """
        ts_code = f"{code}{self._get_stock_suffix(code)}"
        clauses = ["ts_code = :ts_code"]
        params = {"ts_code": ts_code, "limit": limit}

        if start_date:
            clauses.append("trade_date >= :start")
            params["start"] = start_date
        if end_date:
            clauses.append("trade_date <= :end")
            params["end"] = end_date

        where = " AND ".join(clauses)
        sql = text(f"""
            SELECT trade_date, main_net, super_net, big_net, medium_net, small_net
            FROM fund_flow_history
            WHERE {where}
            ORDER BY trade_date ASC
            LIMIT :limit
        """)
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(sql, params).fetchall()
            return [
                {
                    "date": r[0],
                    "main_net": r[1],
                    "super_net": r[2],
                    "big_net": r[3],
                    "medium_net": r[4],
                    "small_net": r[5],
                }
                for r in rows
            ]
        except Exception as e:
            print(f"[DB] 查询资金流向历史失败 {code}: {e}")
            return []
