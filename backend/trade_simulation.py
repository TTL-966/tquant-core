class TradeSimulation:
    def __init__(self, data_file="simulation_data.json"):
        self._data_file = data_file
        self._lock = __import__('threading').Lock()
        loaded = self._load_from_file()
        if loaded:
            self.cash = loaded['cash']
            self.holdings = loaded['holdings']
            self.history = loaded['history']
        else:
            self.cash = 1000000.0
            self.holdings = {}
            self.history = []

    # ---------- 持久化 ----------
    def _file_path(self):
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, self._data_file)

    def _save_to_file(self):
        try:
            with self._lock:
                data = {
                    'cash': self.cash,
                    'holdings': self.holdings,
                    'history': self.history,
                }
            with open(self._file_path(), 'w', encoding='utf-8') as f:
                import json
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[TradeSimulation] 保存失败: {e}")

    def _load_from_file(self):
        import os, json
        path = self._file_path()
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if 'cash' in data and 'holdings' in data:
                return data
        except Exception as e:
            print(f"[TradeSimulation] 加载失败: {e}")
        return None

    def reset(self, initial_cash=1000000.0):
        """重置模拟盘到初始状态。"""
        with self._lock:
            self.cash = initial_cash
            self.holdings = {}
            self.history = []
        self._save_to_file()

    def execute_trade(self, code, action, shares, price, trade_date=None):
        with self._lock:
            record_date = trade_date if trade_date is not None else self._today()
            if action == 'buy':
                cost = round(price * shares, 2)
                if cost > self.cash:
                    return {'success': False, 'message': '资金不足'}
                if code in self.holdings:
                    old = self.holdings[code]
                    new_shares = old['shares'] + shares
                    new_cost = round((old['cost'] * old['shares'] + cost) / new_shares, 2)
                    self.holdings[code] = {'shares': new_shares, 'cost': new_cost}
                else:
                    self.holdings[code] = {'shares': shares, 'cost': price}
                self.cash = round(self.cash - cost, 2)
                self.history.append({
                    'date': record_date,
                    'type': '买入',
                    'code': code,
                    'price': price,
                    'shares': shares
                })
                self._save_to_file()
                return {'success': True, 'message': f'买入{shares}股{code}成功'}

            elif action == 'sell':
                if code not in self.holdings:
                    return {'success': False, 'message': '没有该股票持仓'}
                if self.holdings[code]['shares'] < shares:
                    return {'success': False, 'message': '持仓不足'}
                self.holdings[code]['shares'] -= shares
                if self.holdings[code]['shares'] == 0:
                    del self.holdings[code]
                self.cash = round(self.cash + price * shares, 2)
                self.history.append({
                    'date': record_date,
                    'type': '卖出',
                    'code': code,
                    'price': price,
                    'shares': shares
                })
                self._save_to_file()
                return {'success': True, 'message': f'卖出{shares}股{code}成功'}
            else:
                return {'success': False, 'message': '无效操作'}

    def get_portfolio(self):
        with self._lock:
            holdings_list = []
            total_market = self.cash
            for code, item in self.holdings.items():
                current_price = item['cost']
                market_value = round(current_price * item['shares'], 2)
                profit = round(market_value - item['cost'] * item['shares'], 2)
                holdings_list.append({
                    'code': code,
                    'shares': item['shares'],
                    'cost': item['cost'],
                    'price': current_price,
                    'profit': profit
                })
                total_market += market_value
            return {
                'cash': self.cash,
                'total_assets': round(total_market, 2),
                'holdings': holdings_list,
                'history': list(self.history)
            }

    def _today(self):
        from datetime import datetime
        return datetime.now().strftime('%Y-%m-%d')
