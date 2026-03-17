"""
Tests for agent/cost_tracker.py — CostTracker 成本追踪器。
"""

import pytest
from agent.cost_tracker import CostTracker


class TestCostTracker:
    def test_empty_summary(self):
        ct = CostTracker()
        s = ct.summary()
        assert s["total_calls"] == 0
        assert s["total_input_tokens"] == 0
        assert s["total_cost_usd"] == 0.0
        assert s["cache_hit_rate"] == 0.0

    def test_record_single_call(self):
        ct = CostTracker()
        ct.record("gpt-4o", {"input_tokens": 1000, "output_tokens": 500, "cached_tokens": 0}, "main_loop")
        s = ct.summary()
        assert s["total_calls"] == 1
        assert s["total_input_tokens"] == 1000
        assert s["total_output_tokens"] == 500
        assert s["total_cached_tokens"] == 0
        assert s["cache_hit_rate"] == 0.0
        # cost = 1000 * 2.50/1M + 500 * 10.00/1M = 0.0025 + 0.005 = 0.0075
        assert s["total_cost_usd"] == 0.0075

    def test_record_with_cache(self):
        ct = CostTracker()
        ct.record("gpt-4o", {"input_tokens": 2000, "output_tokens": 100, "cached_tokens": 1500}, "main_loop")
        s = ct.summary()
        assert s["total_cached_tokens"] == 1500
        assert s["cache_hit_rate"] == 0.75
        # non_cached = 500, cost = 500*2.50/1M + 1500*1.25/1M + 100*10.00/1M
        # = 0.00125 + 0.001875 + 0.001 = 0.004125
        assert s["total_cost_usd"] == 0.0041

    def test_record_multiple_calls(self):
        ct = CostTracker()
        ct.record("gpt-4o", {"input_tokens": 1000, "output_tokens": 200, "cached_tokens": 0}, "main_loop")
        ct.record("gpt-4o-mini", {"input_tokens": 500, "output_tokens": 100, "cached_tokens": 0}, "compress")
        s = ct.summary()
        assert s["total_calls"] == 2
        assert s["total_input_tokens"] == 1500
        assert s["total_output_tokens"] == 300

    def test_record_anthropic_model(self):
        ct = CostTracker()
        ct.record("claude-sonnet-4-20250514", {"input_tokens": 1000, "output_tokens": 200, "cached_tokens": 500})
        s = ct.summary()
        assert s["total_calls"] == 1
        assert s["total_cached_tokens"] == 500
        # non_cached = 500, cost = 500*3.00/1M + 500*1.50/1M + 200*15.00/1M
        # = 0.0015 + 0.00075 + 0.003 = 0.00525
        assert s["total_cost_usd"] == 0.0053

    def test_record_unknown_model(self):
        ct = CostTracker()
        ct.record("unknown-model", {"input_tokens": 1000, "output_tokens": 500, "cached_tokens": 0})
        s = ct.summary()
        assert s["total_calls"] == 1
        assert s["total_cost_usd"] == 0.0  # unknown model has 0 pricing

    def test_record_empty_usage(self):
        ct = CostTracker()
        ct.record("gpt-4o", {})
        s = ct.summary()
        assert s["total_calls"] == 0  # empty dict is treated as no usage

    def test_record_none_usage(self):
        ct = CostTracker()
        ct.record("gpt-4o", None)
        s = ct.summary()
        assert s["total_calls"] == 0  # None usage is skipped

    def test_reset(self):
        ct = CostTracker()
        ct.record("gpt-4o", {"input_tokens": 1000, "output_tokens": 500, "cached_tokens": 0})
        ct.reset()
        s = ct.summary()
        assert s["total_calls"] == 0

    def test_purpose_tracking(self):
        ct = CostTracker()
        ct.record("gpt-4o", {"input_tokens": 100, "output_tokens": 50, "cached_tokens": 0}, "main_loop")
        ct.record("gpt-4o-mini", {"input_tokens": 50, "output_tokens": 20, "cached_tokens": 0}, "compress")
        assert ct._calls[0]["purpose"] == "main_loop"
        assert ct._calls[1]["purpose"] == "compress"
