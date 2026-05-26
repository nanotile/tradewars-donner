"""Unit tests for trader.py helper functions: _format_output, _extract_usage."""

from __future__ import annotations

from backend.traders.trader import _format_output, _extract_usage


# ---- _format_output ----

def test_format_output_none():
    assert _format_output(None) == ""


def test_format_output_plain_string():
    assert _format_output("hello") == "hello"


def test_format_output_dict_json():
    assert _format_output({"ticker": "AAPL", "quantity": 10}) == '{"ticker": "AAPL", "quantity": 10}'


def test_format_output_mcp_content_part():
    part = {"type": "input_text", "text": "Price is $142.50"}
    assert _format_output(part) == "Price is $142.50"


def test_format_output_mcp_content_list():
    parts = [
        {"type": "input_text", "text": "First line"},
        {"type": "input_text", "text": "Second line"},
    ]
    assert _format_output(parts) == "First line\nSecond line"


def test_format_output_list_of_strings():
    assert _format_output(["a", "b"]) == "a\nb"


def test_format_output_numeric():
    assert _format_output(42) == "42"
    assert _format_output(3.14) == "3.14"


def test_format_output_mixed_list():
    items = [
        {"type": "input_text", "text": "data here"},
        "raw string",
    ]
    assert _format_output(items) == "data here\nraw string"


def test_format_output_nested_dict_no_input_text():
    d = {"status": "ok", "rows": 5}
    result = _format_output(d)
    assert '"status": "ok"' in result
    assert '"rows": 5' in result


# ---- _extract_usage ----

class FakeUsage:
    def __init__(self, input_tokens=0, output_tokens=0, input_details=None, output_details=None):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.input_tokens_details = input_details
        self.output_tokens_details = output_details


class FakeInputDetails:
    def __init__(self, cached_tokens=0):
        self.cached_tokens = cached_tokens


class FakeOutputDetails:
    def __init__(self, reasoning_tokens=0):
        self.reasoning_tokens = reasoning_tokens


class FakeResponse:
    def __init__(self, usage=None):
        self.usage = usage


class FakeResult:
    def __init__(self, raw_responses=None):
        self.raw_responses = raw_responses


def test_extract_usage_none_result():
    assert _extract_usage(None) is None


def test_extract_usage_no_responses():
    result = FakeResult(raw_responses=[])
    assert _extract_usage(result) is None


def test_extract_usage_no_raw_responses_attr():
    assert _extract_usage("not a result") is None


def test_extract_usage_single_response():
    usage = FakeUsage(input_tokens=100, output_tokens=50)
    result = FakeResult(raw_responses=[FakeResponse(usage=usage)])
    out = _extract_usage(result)
    assert out == {
        "input_tokens": 100,
        "output_tokens": 50,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
    }


def test_extract_usage_multiple_responses_summed():
    u1 = FakeUsage(input_tokens=100, output_tokens=50)
    u2 = FakeUsage(input_tokens=200, output_tokens=75)
    result = FakeResult(raw_responses=[FakeResponse(u1), FakeResponse(u2)])
    out = _extract_usage(result)
    assert out["input_tokens"] == 300
    assert out["output_tokens"] == 125


def test_extract_usage_with_cached_and_reasoning():
    usage = FakeUsage(
        input_tokens=500,
        output_tokens=200,
        input_details=FakeInputDetails(cached_tokens=300),
        output_details=FakeOutputDetails(reasoning_tokens=150),
    )
    result = FakeResult(raw_responses=[FakeResponse(usage=usage)])
    out = _extract_usage(result)
    assert out == {
        "input_tokens": 500,
        "output_tokens": 200,
        "cached_tokens": 300,
        "reasoning_tokens": 150,
    }


def test_extract_usage_skips_responses_with_no_usage():
    u1 = FakeUsage(input_tokens=100, output_tokens=50)
    result = FakeResult(raw_responses=[FakeResponse(None), FakeResponse(u1)])
    out = _extract_usage(result)
    assert out["input_tokens"] == 100
    assert out["output_tokens"] == 50


def test_extract_usage_all_zero_returns_none():
    usage = FakeUsage(input_tokens=0, output_tokens=0)
    result = FakeResult(raw_responses=[FakeResponse(usage=usage)])
    assert _extract_usage(result) is None
