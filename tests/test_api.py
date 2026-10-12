"""End-to-end API test with the real mpv engine on the null audio output.

Skipped automatically when mpv/ffmpeg are not installed.
"""

import shutil
import subprocess
import time

import pytest

pytestmark = pytest.mark.skipif(not (shutil.which("mpv") and shutil.which("ffmpeg")), reason="needs mpv + ffmpeg")

H = {"X-Rolola": "1"}


@pytest.fixture(scope="module")
def client(tmp_path_factory, request):
    from starlette.testclient import TestClient

    root = tmp_path_factory.mktemp("jb")
    music = root / "music" / "Artist" / "Album"
    music.mkdir(parents=True)
    for i, title in enumerate(["Alpha", "Bravo", "Charlie", "Delta"], start=1):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=f={300 + i * 50}:d=120",
                        "-metadata", f"title={title}", "-metadata", "artist=Artist", "-metadata", "album=Album",
                        "-metadata", f"track={i}", str(music / f"0{i} {title}.mp3")], check=True)
    mp = pytest.MonkeyPatch()
    for k, v in {"DATA_DIR": str(root / "data"), "MUSIC_DIR": str(root / "music"), "AUDIO_DEVICE": "null",
                 "ADMIN_PASSWORD": "admin-pass-123", "YTDLP_AUTO_UPDATE": "false", "LOG_JSON": "false",
                 "ROLOLA_DEFAULT_AUTOPLAY_MODE": "off", "ROLOLA_DEFAULT_VOLUME_COOLDOWN_S": "30"}.items():
        mp.setenv(k, v)
    from rolola.app import create_app

    app = create_app()
    with TestClient(app, base_url="http://testserver") as c:
        # wait for the library scan
        for _ in range(50):
            if app.svc.db.scalar("SELECT COUNT(*) FROM library") == 4:
                break
            time.sleep(0.1)
        c.app_svc = app.svc
        yield c
    mp.undo()


def join(client, name):
    from starlette.testclient import TestClient

    c = TestClient(client.app, base_url="http://testserver")
    c.portal = client.portal  # share the app's event loop (background tasks must survive the request)
    client.app_svc.ratelimit._buckets.clear()  # many "people" join from one test IP
    r = c.post("/api/session/join", json={"name": name})
    assert r.status_code == 200, r.text
    return c


def search(c, q):
    r = c.get("/api/search", params={"q": q, "source": "local"})
    assert r.status_code == 200
    return [x for x in r.json()["results"] if x["kind"] == "track"]


def wait_for(fn, timeout=25.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("condition not met")


def test_health_and_static(client):
    assert client.get("/healthz").json()["engine"] is True
    assert "<title>" in client.get("/").text
    assert client.get("/api/openapi.json").status_code == 200


def test_requires_name_and_csrf_header(client):
    assert client.get("/api/search", params={"q": "a", "source": "local"}).status_code == 401
    ana = join(client, "Ana")
    t = search(ana, "alpha")[0]
    r = ana.post("/api/queue", json={"track": t})  # no X-Rolola header
    assert r.status_code == 403 and r.json()["error"] == "err.csrf"


def test_queue_fairness_votes_and_owner_skip(client):
    ana, ben, cy = join(client, "Ana2"), join(client, "Ben"), join(client, "Cy")
    for q in ("alpha", "bravo", "charlie"):
        assert ana.post("/api/queue", json={"track": search(ana, q)[0]}, headers=H).status_code == 200
    ben.post("/api/queue", json={"track": search(ben, "delta")[0]}, headers=H)
    state = wait_for(lambda: (s := ana.get("/api/state").json())["now"] and s)
    assert state["now"]["user"]["name"] == "Ana2"            # first track starts
    order = [q["user"]["name"] for q in state["queue"]]
    assert order[0] == "Ben"                                  # Ben jumps ahead of Ana's 2nd/3rd

    with ana.websocket_connect("/api/ws"), ben.websocket_connect("/api/ws"), cy.websocket_connect("/api/ws"):
        time.sleep(0.3)
        playing = ana.get("/api/state").json()["now"]["play_id"]
        r = cy.post("/api/player/vote", headers=H).json()    # 1 of 2 required (50% of 3)
        assert r["skipped"] is False
        assert ana.get("/api/state").json()["votes"]["count"] == 1
        ben.post("/api/player/vote", headers=H)               # 2 of 2 -> skip
        wait_for(lambda: (ana.get("/api/state").json()["now"] or {}).get("play_id") != playing)
        now = wait_for(lambda: (s := ana.get("/api/state").json())["now"] and s["now"]["user"]["name"] == "Ben" and s)
        assert now["votes"]["count"] == 0
        r = ben.post("/api/player/skip", headers=H).json()    # owner skips own track immediately
        assert r["skipped"] is True

    history = ana.get("/api/history").json()
    outcomes = {h["track"]["title"]: h["outcome"] for h in history if h["outcome"]}
    assert outcomes.get("Alpha") == "voted" and outcomes.get("Delta") == "skipped"


def test_users_cannot_remove_others_tracks(client):
    client.app_svc.player.queue.clear()
    a, b = join(client, "Dee"), join(client, "Eve")
    item = a.post("/api/queue", json={"track": search(a, "charlie")[0]}, headers=H).json()["ids"][0]
    assert b.delete(f"/api/queue/{item}", headers=H).status_code == 400
    assert a.delete(f"/api/queue/{item}", headers=H).status_code == 200


def test_volume_safety_rails(client):
    u = join(client, "Fay")
    r = u.post("/api/player/volume", json={"volume": 100}, headers=H).json()
    assert r["volume"] == r["max"] == 85                       # clamped to max_volume
    r = u.post("/api/player/volume", json={"volume": 40}, headers=H)
    assert r.status_code == 429 and r.json()["error"] == "err.volume_cooldown"


def test_admin_login_and_admin_only_endpoints(client):
    u = join(client, "Gus")
    assert u.get("/api/admin/settings").status_code == 403
    assert u.post("/api/session/admin", json={"password": "wrong"}, headers=H).status_code == 403
    assert u.post("/api/session/admin", json={"password": "admin-pass-123"}, headers=H).status_code == 200
    s = u.get("/api/admin/settings").json()["settings"]
    assert s["fairness_enabled"]["value"] is True
    assert u.put("/api/admin/settings", json={"values": {"max_volume": 70}}, headers=H).status_code == 200
    assert u.get("/api/state").json()["volume"] <= 70
    diag = u.get("/api/admin/diagnostics").json()
    assert diag["audio"]["engine_alive"] and diag["library"]["tracks"] == 4
    # secrets never come back in clear text
    u.put("/api/admin/providers/jamendo", json={"values": {"client_id": "my-secret-id"}}, headers=H)
    prov = next(p for p in u.get("/api/admin/providers").json() if p["id"] == "jamendo")
    assert prov["config"]["client_id"] == "***"


def test_blocklist_blocks_adding(client):
    admin = join(client, "Hal")
    admin.post("/api/session/admin", json={"password": "admin-pass-123"}, headers=H)
    admin.post("/api/admin/blocklist", json={"kind": "keyword", "value": "bravo"}, headers=H)
    u = join(client, "Ivy")
    r = u.post("/api/queue", json={"track": search(u, "bravo")[0]}, headers=H)
    assert r.status_code == 400 and r.json()["error"] == "err.nothing_added"


def test_duplicates_are_refused(client):
    client.app_svc.player.queue.clear()
    u = join(client, "Jo")
    t = search(u, "delta")[0]
    assert u.post("/api/queue", json={"track": t}, headers=H).status_code == 200
    r = join(client, "Kim").post("/api/queue", json={"track": t}, headers=H)
    assert r.status_code == 400 and "already in the queue" in r.json()["params"]["reasons"][0]
