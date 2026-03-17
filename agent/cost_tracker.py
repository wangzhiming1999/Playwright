"""
LLM 调用成本追踪器。

记录每次 LLM 调用的 token 消耗，按模型价格表计算成本，
支持缓存命中率统计。
"""


class CostTracker:
    """追踪每次 LLM 调用的 token 消耗和成本。"""

    # 价格表（每 1M tokens，USD）
    PRICING = {
        "gpt-4o":                       {"input": 2.50, "output": 10.00, "cached_input": 1.25},
        "gpt-4o-mini":                  {"input": 0.15, "output": 0.60,  "cached_input": 0.075},
        "claude-sonnet-4-20250514":     {"input": 3.00, "output": 15.00, "cached_input": 1.50},
        "claude-haiku-4-5-20251001":    {"input": 0.80, "output": 4.00,  "cached_input": 0.40},
        "claude-opus-4-20250514":       {"input": 15.00, "output": 75.00, "cached_input": 7.50},
    }

    def __init__(self):
        self._calls: list[dict] = []

    def record(self, model: str, usage: dict, purpose: str = ""):
        """记录一次 LLM 调用。"""
        if not usage:
            return

        pricing = self.PRICING.get(model, {"input": 0, "output": 0, "cached_input": 0})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cached_tokens = usage.get("cached_tokens", 0)
        non_cached = max(input_tokens - cached_tokens, 0)

        cost = (
            non_cached * pricing["input"] / 1_000_000
            + cached_tokens * pricing["cached_input"] / 1_000_000
            + output_tokens * pricing["output"] / 1_000_000
        )

        self._calls.append({
            "model": model,
            "purpose": purpose,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "cost_usd": round(cost, 6),
        })

    def summary(self) -> dict:
        """返回汇总统计。"""
        if not self._calls:
            return {
                "total_calls": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cached_tokens": 0,
                "cache_hit_rate": 0.0,
                "total_cost_usd": 0.0,
            }

        total_input = sum(c["input_tokens"] for c in self._calls)
        total_output = sum(c["output_tokens"] for c in self._calls)
        total_cached = sum(c["cached_tokens"] for c in self._calls)
        total_cost = sum(c["cost_usd"] for c in self._calls)
        cache_hit_rate = total_cached / total_input if total_input > 0 else 0.0

        return {
            "total_calls": len(self._calls),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cached_tokens": total_cached,
            "cache_hit_rate": round(cache_hit_rate, 3),
            "total_cost_usd": round(total_cost, 4),
        }

    def reset(self):
        """重置所有记录。"""
        self._calls.clear()
