"""Creator profiles: setting a GitHub username (PATCH /me), the badge it unlocks on a profile, and the cached list of
their kino-plugin repos (GET /users/<id>/plugins), with a fake GitHub so no test touches the real network. [test_parse]
below covers the raw-response parsing (`creators._parse`) on its own, with no app or network at all.
"""
import pytest
from fastapi.testclient import TestClient

from app import creators
from app.main import create_app


def _plugin(repo, stars=0, description="A plugin", name=None):
    """What `creators.fetch` (and so `github_fetch`) returns for one repo: already parsed, our own shape."""
    return {"repo": repo.lower(), "name": name or repo.split("/")[-1], "description": description, "stars": stars}


@pytest.fixture
def github():
    """A fake replacement for `creators.fetch`: `github.repos[username]` is the (already-parsed) list it answers;
    `github.calls` counts requests; `github.fail` makes the next call raise, to test the cached-fallback path."""
    class Fake:
        def __init__(self):
            self.repos = {}
            self.calls = 0
            self.fail = False

        def __call__(self, username):
            self.calls += 1
            if self.fail:
                raise RuntimeError("github is down")
            return self.repos.get(username.lower(), [])

    return Fake()


@pytest.fixture
def client(database, public_b64, github):
    database.truncate_all()
    app = create_app(database, public_key_b64=public_b64, secret=b"test-secret", github_fetch=github)
    with TestClient(app) as c:
        c.app_db = database
        yield c


def test_setting_and_clearing_the_github_username(client, login):
    a = login(1, "ana")
    assert client.get("/me", headers=a).json().get("githubUsername") is None
    r = client.patch("/me", json={"githubUsername": "LordMacu"}, headers=a)
    assert r.status_code == 200, r.text
    assert r.json()["githubUsername"] == "LordMacu"
    assert client.get("/me", headers=a).json()["githubUsername"] == "LordMacu"
    # Visible on the public profile too, exactly because it is the point of the badge.
    b = login(2, "beto")
    me = client.get("/me", headers=a).json()["id"]
    assert client.get(f"/users/{me}", headers=b).json()["user"]["githubUsername"] == "LordMacu"
    # Clearing it (null) removes it from both.
    assert client.patch("/me", json={"githubUsername": None}, headers=a).json().get("githubUsername") is None
    assert "githubUsername" not in client.get(f"/users/{me}", headers=b).json()["user"]


def test_a_username_that_is_not_a_valid_github_handle_is_refused(client, login):
    a = login(1, "ana")
    for bad in ("-leading", "trailing-", "double--hyphen", "with space", "x" * 40):
        assert client.patch("/me", json={"githubUsername": bad}, headers=a).json()["error"] == "bad_github_username", bad
    # Blank, or just "@", clears it like null: a person backspacing the field should not get an error.
    assert client.patch("/me", json={"githubUsername": "@"}, headers=a).json().get("githubUsername") is None
    # A leading @ is stripped, same as handle.
    assert client.patch("/me", json={"githubUsername": "@lordmacu"}, headers=a).json()["githubUsername"] == "lordmacu"


def test_someone_without_a_github_username_has_no_plugin_list(client, login):
    a = login(1, "ana")
    me = client.get("/me", headers=a).json()["id"]
    assert client.get(f"/users/{me}/plugins", headers=a).status_code == 404


def test_the_plugin_list_is_fetched_once_and_cached(client, login, github):
    a = login(1, "ana")
    client.patch("/me", json={"githubUsername": "lordmacu"}, headers=a)
    me = client.get("/me", headers=a).json()["id"]
    github.repos["lordmacu"] = [_plugin("lordmacu/kino-plugin-foo", stars=12, description="Foo"), _plugin("lordmacu/Kino-Plugin-Bar")]
    r = client.get(f"/users/{me}/plugins", headers=a)
    assert r.status_code == 200, r.text
    assert r.json()["plugins"] == [
        {"repo": "lordmacu/kino-plugin-foo", "name": "kino-plugin-foo", "description": "Foo", "stars": 12},
        {"repo": "lordmacu/kino-plugin-bar", "name": "Kino-Plugin-Bar", "description": "A plugin", "stars": 0},
    ]
    assert github.calls == 1
    # A second read, same minute: answered from cache, GitHub not asked again (even if the list there changed).
    github.repos["lordmacu"] = []
    assert len(client.get(f"/users/{me}/plugins", headers=a).json()["plugins"]) == 2
    assert github.calls == 1


def test_a_failed_fetch_falls_back_to_what_is_cached_or_an_empty_list(client, login, github):
    a = login(1, "ana")
    client.patch("/me", json={"githubUsername": "ana"}, headers=a)
    me = client.get("/me", headers=a).json()["id"]
    # Never fetched before, and GitHub is down: an empty list, not a 500.
    github.fail = True
    assert client.get(f"/users/{me}/plugins", headers=a).json()["plugins"] == []

    # Populate the cache, then force staleness and fail: the last good list stands.
    github.fail = False
    github.repos["ana"] = [_plugin("ana/kino-plugin-one")]
    client.get(f"/users/{me}/plugins", headers=a)
    # Force the cached row to look stale (older than CACHE_MS) by rewriting fetched_at directly.
    client.app_db.execute("UPDATE creator_plugins SET fetched_at = 0 WHERE github_username = 'ana'")
    github.fail = True
    r = client.get(f"/users/{me}/plugins", headers=a)
    assert r.json()["plugins"] == [{"repo": "ana/kino-plugin-one", "name": "kino-plugin-one", "description": "A plugin", "stars": 0}]


def test_parse_skips_an_item_without_a_plain_owner_slash_name():
    assert creators._parse([{"full_name": "no-slash-here", "stargazers_count": 1}]) == []


def test_parse_lowercases_the_repo_but_not_the_shown_name():
    raw = {"full_name": "LordMacu/Kino-Plugin-Bar", "stargazers_count": 3, "description": None}
    assert creators._parse([raw]) == [{"repo": "lordmacu/kino-plugin-bar", "name": "Kino-Plugin-Bar", "description": "", "stars": 3}]


def test_parse_is_fine_with_no_items_at_all():
    assert creators._parse(None) == []
    assert creators._parse([]) == []


def test_changing_the_github_username_is_rate_limited_like_the_handle(client, login):
    a = login(1, "ana")
    for i in range(10):
        assert client.patch("/me", json={"githubUsername": f"name{i}"}, headers=a).status_code == 200
    assert client.patch("/me", json={"githubUsername": "name11"}, headers=a).json()["error"] == "rate_limited"
