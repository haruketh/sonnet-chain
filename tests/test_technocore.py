import json

import httpx
import pytest

from sonnet_chain.technocore import Technocore


def test_gap_uses_export_and_preserves_generation():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/export"):
            rows = [json.dumps({"seq": i, "from": "x", "text": str(i)}) for i in range(1, 4)]
            return httpx.Response(200, text="\n".join(rows), headers={"X-Room-Generation": "7"})
        return httpx.Response(200, json={"first_seq": 3, "generation": 7, "messages": [{"seq": 3, "text": "3"}]})

    tc = Technocore("https://example.test")
    tc.client.close()
    tc.client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        records, generation = tc.read_page("room", since=0)
    finally:
        tc.close()
    assert [record.seq for record in records] == [1, 2, 3]
    assert generation == 7


def test_explicit_export_history_supports_policy_gap_repair():
    rows = "\n".join(json.dumps({"seq": i, "from": "x", "text": str(i)}) for i in range(4, 7))
    tc = Technocore("https://example.test")
    tc.client.close()
    tc.client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text=rows, headers={"X-Room-Generation": "9"})
    ))
    try:
        records, generation = tc.export_history("room")
    finally:
        tc.close()
    assert [item.seq for item in records] == [4, 5, 6]
    assert generation == 9


def test_watch_retries_read_timeout_from_same_cursor(monkeypatch):
    seen_since = []
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        seen_since.append(request.url.params["since"])
        if calls == 1:
            raise httpx.ReadTimeout("temporary", request=request)
        return httpx.Response(200, json={"messages": [{"seq": 8, "text": "ok"}]})

    monkeypatch.setattr("sonnet_chain.technocore.time.sleep", lambda _: None)
    tc = Technocore("https://example.test")
    tc.client.close()
    tc.client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        assert next(tc.watch("room", since=7)).seq == 8
    finally:
        tc.close()
    assert seen_since == ["7", "7"]


def test_watch_retries_5xx_and_keeps_last_delivered_cursor(monkeypatch):
    seen_since = []
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        seen_since.append(request.url.params["since"])
        if calls == 1:
            return httpx.Response(200, json={"messages": [{"seq": 4, "text": "first"}]})
        if calls == 2:
            return httpx.Response(503, json={"error": "temporary"})
        return httpx.Response(200, json={"messages": [
            {"seq": 4, "text": "duplicate"}, {"seq": 5, "text": "second"},
        ]})

    monkeypatch.setattr("sonnet_chain.technocore.time.sleep", lambda _: None)
    tc = Technocore("https://example.test")
    tc.client.close()
    tc.client = httpx.Client(transport=httpx.MockTransport(handler))
    stream = tc.watch("room", since=3)
    try:
        assert next(stream).seq == 4
        assert next(stream).seq == 5
    finally:
        stream.close()
        tc.close()
    assert seen_since == ["3", "4", "4"]


def test_watch_does_not_retry_non_transient_http_error(monkeypatch):
    monkeypatch.setattr("sonnet_chain.technocore.time.sleep", lambda _: None)
    tc = Technocore("https://example.test")
    tc.client.close()
    tc.client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(401)))
    stream = tc.watch("room")
    try:
        with pytest.raises(httpx.HTTPStatusError):
            next(stream)
    finally:
        stream.close()
        tc.close()
