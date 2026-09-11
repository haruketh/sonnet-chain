import pytest

from sonnet_chain.config import CONTEST_ID, ROOMS, ContestRooms
from sonnet_chain.protocol import register_writer, roster, submit, team_request, word


def test_all_room_names_derive_from_contest_id():
    rooms = ContestRooms("sonnet-9")
    assert rooms.rules == "d-sonnet-9-rules"
    assert rooms.registration == "mb-sonnet-9-registration"
    assert rooms.discovery == "mb-sonnet-9-discovery"
    assert rooms.campaign == "mb-sonnet-9-campaign"
    assert rooms.votes == "mb-sonnet-9-votes"
    assert rooms.submissions == "mb-sonnet-9-submissions"
    assert rooms.results == "d-sonnet-9-results"
    assert rooms.team("alpha_1") == "d-sonnet-9-team-alpha_1"


def test_active_namespace_is_sonnet_2():
    assert CONTEST_ID == "sonnet-2"
    assert ROOMS.rules == "d-sonnet-2-rules"


def test_registration_shape():
    x = register_writer("https://x.com/sarukubt", "r1")
    assert x == {
        "type": "sonnet.register.v1",
        "contest_id": "sonnet-2",
        "role": "writer",
        "x_account_url": "https://x.com/sarukubt",
        "request_id": "r1",
    }


def test_roster_4_to_8():
    members = [f"did:key:z{i}" for i in range(4)]
    x = roster("abc", ROOMS.team("abc"), 0, members, "r2")
    assert x["members"] == members


def test_old_contest_team_room_is_rejected():
    members = [f"did:key:z{i}" for i in range(4)]
    with pytest.raises(ValueError, match="active contest"):
        roster("abc", "d-sonnet-1-team-abc", 0, members, "r2")
    with pytest.raises(ValueError, match="active contest"):
        submit("abc", "d-sonnet-1-team-abc", 0, 98, "a" * 64, ["post"], "r5")


def test_word_shape():
    x = word("abc", 7, 12, "deadbeef", "dream", "r3")
    assert x["type"] == "sonnet.word.v1"
    assert x["version"] == 12
    assert x["previous_state_hash"] == "deadbeef"


def test_game_id():
    assert team_request("saruku1", "r4")["game_id"] == "saruku1"
