# backend/strategy_storage.py
import os
import json

class StrategyStorage:
    def __init__(self):
        # 确定 strategies 文件夹路径（与 backend 同级）
        base_dir = os.path.dirname(os.path.abspath(__file__))           # backend/
        self.strategies_dir = os.path.join(os.path.dirname(base_dir), 'strategies')
        os.makedirs(self.strategies_dir, exist_ok=True)
        self.file_path = os.path.join(self.strategies_dir, 'strategies.json')
        self.strategies = []
        self._load()

    def _load(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    self.strategies = json.load(f)
            except Exception:
                self.strategies = []
        else:
            self.strategies = []

    def _save(self):
        with open(self.file_path, 'w', encoding='utf-8') as f:
            json.dump(self.strategies, f, ensure_ascii=False, indent=2)

    def list_strategies(self):
        return self.strategies

    def get_strategy(self, strategy_id):
        for s in self.strategies:
            if s['id'] == strategy_id:
                return s
        return None

    def save_strategy(self, name, code, strategy_id=None):
        if strategy_id and strategy_id != 0:
            # 更新已有策略
            for s in self.strategies:
                if s['id'] == strategy_id:
                    s['name'] = name
                    s['code'] = code
                    self._save()
                    return s
            # 如果未找到则视为新建
        # 新建
        new_id = max([s['id'] for s in self.strategies], default=0) + 1
        new_strategy = {
            'id': new_id,
            'name': name,
            'code': code
        }
        self.strategies.append(new_strategy)
        self._save()
        return new_strategy

    def delete_strategy(self, strategy_id):
        before = len(self.strategies)
        self.strategies = [s for s in self.strategies if s['id'] != strategy_id]
        if len(self.strategies) < before:
            self._save()
            return True
        return False
