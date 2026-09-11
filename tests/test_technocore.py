import json

import httpx

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
