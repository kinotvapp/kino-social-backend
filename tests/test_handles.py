def test_new_user_has_no_handle_until_chosen(client, login):
    a = login(1)
    assert client.get("/me", headers=a).json()["handle"] is None
    me = client.patch("/me", json={"handle": "Ana_Maria"}, headers=a).json()
    assert me["handle"] == "ana_maria"


def test_handle_is_unique_case_insensitive(client, login):
    a = login(1)
    b = login(2)
    client.patch("/me", json={"handle": "cine.fan"}, headers=a)
    r = client.patch("/me", json={"handle": "CINE.FAN"}, headers=b)
    assert r.status_code == 409 and r.json()["error"] == "handle_taken"
    assert client.get("/handles/cine.fan", headers=b).json() == {"handle": "cine.fan", "available": False}
    assert client.get("/handles/otro_nombre", headers=b).json() == {"handle": "otro_nombre", "available": True}
    # Changing your own handle frees the old one.
    client.patch("/me", json={"handle": "cinefan2"}, headers=a)
    assert client.patch("/me", json={"handle": "cine.fan"}, headers=b).status_code == 200


def test_handle_rules(client, login):
    a = login(1)
    for bad in ["ab", "a" * 21, "con espacio", "ñandu", "kino", "admin", "soporte", "mierda", "hijueputa_1", ".punto", "doble..punto"]:
        r = client.patch("/me", json={"handle": bad}, headers=a)
        assert r.status_code == 400, bad
    assert client.get("/handles/admin", headers=a).json()["available"] is False


def test_search_people_by_handle_or_name(client, login):
    a = login(1)
    b = login(2, "Beatriz Gómez")
    c = login(3, "Carlos")
    client.patch("/me", json={"handle": "bea_cine"}, headers=b)
    client.patch("/me", json={"handle": "carlitos"}, headers=c)
    by_handle = client.get("/people?q=@bea", headers=a).json()["items"]
    assert [p["handle"] for p in by_handle] == ["bea_cine"]
    by_name = client.get("/people?q=gomez", headers=a).json()["items"]  # accents and case ignored
    assert [p["id"] for p in by_name] == [2]
    assert client.get("/people?q=x", headers=a).status_code == 400  # at least 2 characters
    assert [p["id"] for p in client.get("/people?q=ana", headers=a).json()["items"]] == []  # never yourself


def test_search_skips_people_who_never_picked_a_handle(client, login):
    a = login(1)
    login(2, "Andrea")  # never finished the welcome: no @handle yet
    d = login(3, "Andrés")
    client.patch("/me", json={"handle": "andres"}, headers=d)
    assert [p["handle"] for p in client.get("/people?q=and", headers=a).json()["items"]] == ["andres"]
