"""Creator profiles: `creators.refresh` pulling kino-plugin-store's public index into `creator_plugins` (§1a/§1b),
hiding/unhiding one of your own rows (§1c), and the two reads (`GET /users/<id>/plugins`, `GET /me/plugins`, §3). No
test touches the real network or GitHub -- [index] below stands in for a fetched `index.json`. [test_parse_*] covers
the raw-index parsing (`creators._parse`) on its own, with no app, no database, no network at all.
"""
import pytest
from fastapi.testclient import TestClient

from app import creators
from app.main import create_app


def _entry(address, creator=None, stars=0, description="A plugin", name=None):
    """One `index.json` entry, as `kino-plugin-store`'s `entries.js` would write it."""
    e = {"address": address, "popularity": stars, "description": description, "name": name or address.split("/")[-1]}
    if creator is not None:
        e["creator"] = creator
    return e


@pytest.fixture
def client(database, public_b64):
    database.truncate_all()
    app = create_app(database, public_key_b64=public_b64, secret=b"test-secret")
    with TestClient(app) as c:
        c.app_db = database
        yield c


def _handle(client, headers, handle):
    assert client.patch("/me", json={"handle": handle}, headers=headers).status_code == 200


def test_parse_skips_an_entry_with_no_creator_or_a_malformed_one():
    for bad in [{"address": "a/b"}, {"address": "a/b", "creator": ""}, {"address": "a/b", "creator": "ab"},
                {"address": "a/b", "creator": "a" * 21}, {"address": "a/b", "creator": "with space"},
                {"address": "a/b", "creator": 42}, {"address": "no-slash", "creator": "lordmacu"}]:
        assert creators._parse({"entries": [bad]}) == [], bad


def test_parse_lowercases_the_handle_and_the_repo_but_not_the_shown_name():
    entries = [_entry("LordMacu/Kino-Plugin-Bar", creator="LordMacu", stars=3, description=None, name=None)]
    entries[0]["description"] = None
    assert creators._parse({"entries": entries}) == [
        {"kino_user": "lordmacu", "repo": "lordmacu/kino-plugin-bar", "name": "Kino-Plugin-Bar", "description": "", "stars": 3},
    ]


def test_parse_is_fine_with_no_entries_at_all():
    assert creators._parse({}) == []
    assert creators._parse({"entries": []}) == []
    assert creators._parse(None) == []


def test_refresh_writes_the_table_and_keeps_hidden_flags_across_a_later_refresh(client):
    conn = client.app_db
    found = [_entry("lordmacu/kino-plugin-foo", creator="lordmacu", stars=12, description="Foo"),
              _entry("ana/kino-plugin-one", creator="ana", stars=1)]
    assert creators.refresh(conn, fetch=lambda: {"entries": found}) == 2
    conn.execute("UPDATE creator_plugins SET hidden = TRUE WHERE kino_user = 'lordmacu' AND repo = 'lordmacu/kino-plugin-foo'")

    # A later refresh that still names the same pair keeps it hidden, updates its stars, and drops a repo gone from the index.
    found2 = [_entry("lordmacu/kino-plugin-foo", creator="lordmacu", stars=20, description="Foo v2")]
    assert creators.refresh(conn, fetch=lambda: {"entries": found2}) == 1
    rows = {(r["kino_user"], r["repo"]): r for r in conn.execute("SELECT * FROM creator_plugins").fetchall()}
    assert ("ana", "ana/kino-plugin-one") not in rows
    row = rows[("lordmacu", "lordmacu/kino-plugin-foo")]
    assert row["hidden"] is True and row["stars"] == 20 and row["description"] == "Foo v2"


def test_a_failed_fetch_leaves_the_table_as_it_was(client):
    conn = client.app_db
    creators.refresh(conn, fetch=lambda: {"entries": [_entry("lordmacu/kino-plugin-foo", creator="lordmacu", stars=5)]})

    def boom():
        raise RuntimeError("github is down")

    assert creators.refresh(conn, fetch=boom) == -1
    rows = conn.execute("SELECT * FROM creator_plugins").fetchall()
    assert [r["repo"] for r in rows] == ["lordmacu/kino-plugin-foo"]


def test_someone_with_no_attached_plugin_has_an_empty_list_never_a_404(client, login):
    a = login(1, "ana")
    me = client.get("/me", headers=a).json()["id"]
    assert client.get(f"/users/{me}/plugins", headers=a).json() == {"plugins": []}


def test_the_plugin_list_is_whatever_refresh_wrote_for_that_handle(client, login):
    a = login(1, "ana")
    _handle(client, a, "lordmacu")
    me = client.get("/me", headers=a).json()["id"]
    creators.refresh(client.app_db, fetch=lambda: {"entries": [
        _entry("lordmacu/kino-plugin-foo", creator="lordmacu", stars=12, description="Foo"),
        _entry("someoneelse/kino-plugin-bar", creator="otheruser", stars=9),
    ]})
    assert client.get(f"/users/{me}/plugins", headers=a).json()["plugins"] == [
        {"repo": "lordmacu/kino-plugin-foo", "name": "kino-plugin-foo", "description": "Foo", "stars": 12},
    ]


def test_hiding_and_unhiding_your_own_attached_plugin(client, login):
    a = login(1, "ana")
    _handle(client, a, "lordmacu")
    me = client.get("/me", headers=a).json()["id"]
    creators.refresh(client.app_db, fetch=lambda: {"entries": [_entry("lordmacu/kino-plugin-foo", creator="lordmacu", stars=12, description="Foo")]})

    assert len(client.get(f"/users/{me}/plugins", headers=a).json()["plugins"]) == 1
    r = client.post("/me/plugins/lordmacu/kino-plugin-foo/hide", headers=a)
    assert r.status_code == 200, r.text
    # Hidden on the public read, gone from someone else's view too.
    assert client.get(f"/users/{me}/plugins", headers=a).json()["plugins"] == []
    b = login(2, "beto")
    assert client.get(f"/users/{me}/plugins", headers=b).json()["plugins"] == []
    # Still there, flagged, on your own "all" read.
    all_plugins = client.get("/me/plugins?all=1", headers=a).json()["plugins"]
    assert all_plugins == [{"repo": "lordmacu/kino-plugin-foo", "name": "kino-plugin-foo", "description": "Foo", "stars": 12, "hidden": True}]
    assert client.get("/me/plugins", headers=a).json()["plugins"] == []

    assert client.post("/me/plugins/lordmacu/kino-plugin-foo/unhide", headers=a).status_code == 200
    assert len(client.get(f"/users/{me}/plugins", headers=a).json()["plugins"]) == 1


def test_hiding_a_repo_that_never_named_you_is_404(client, login):
    a = login(1, "ana")
    _handle(client, a, "lordmacu")
    creators.refresh(client.app_db, fetch=lambda: {"entries": [_entry("otheruser/kino-plugin-foo", creator="otheruser", stars=1)]})
    assert client.post("/me/plugins/otheruser/kino-plugin-foo/hide", headers=a).status_code == 404


def test_the_profile_response_carries_no_github_username_field_any_more(client, login):
    a = login(1, "ana")
    assert "githubUsername" not in client.get("/me", headers=a).json()
    me = client.get("/me", headers=a).json()["id"]
    assert "githubUsername" not in client.get(f"/users/{me}", headers=a).json()["user"]
