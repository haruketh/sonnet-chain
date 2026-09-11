from sonnet_chain.protocol import register_writer, roster, team_request, word


def test_registration_shape():
    x = register_writer("https://x.com/sarukubt", "r1")
    assert x == {
        "type": "sonnet.register.v1",
        "contest_id": "sonnet-1",
        "role": "writer",
        "x_account_url": "https://x.com/sarukubt",
        "request_id": "r1",
    }


def test_roster_4_to_8():
    members = [f"did:key:z{i}" for i in range(4)]
    x = roster("abc", "d-sonnet-1-team-abc", 0, members, "r2")
    assert x["members"] == members


def test_word_shape():
    x = word("abc", 7, 12, "deadbeef", "dream", "r3")
    assert x["type"] == "sonnet.word.v1"
    assert x["version"] == 12
    assert x["previous_state_hash"] == "deadbeef"


def test_game_id():
    assert team_request("saruku1", "r4")["game_id"] == "saruku1"
