"""Name-only guests: same name = same person, idle people and devices expire."""

import time

import pytest

from rolola.auth import Auth, merge_duplicate_guests
from rolola.config import load_config
from rolola.db import Database
from rolola.housekeeping import cleanup
from rolola.settings import Secrets, Settings

DAY = 86400


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "x-admin-pass")
    cfg = load_config()
    cfg.data_dir = tmp_path
    db = Database(tmp_path / "g.db")
    return db, Auth(cfg, db, Settings(db, {}, Secrets(tmp_path, "")))


def age(db, uid, days):
    t = time.time() - days * DAY
    db.execute("UPDATE users SET last_seen=? WHERE id=?", (t, uid))
    db.execute("UPDATE sessions SET last_seen=? WHERE user_id=?", (t, uid))


def test_same_name_on_another_device_is_the_same_person(env):
    db, auth = env
    pc, _ = auth.create_guest("Juan")
    incognito, _ = auth.create_guest("  juan ")
    assert pc.user_id == incognito.user_id and incognito.name == "Juan"
    assert db.scalar("SELECT COUNT(*) FROM users") == 1
    assert db.scalar("SELECT COUNT(*) FROM sessions") == 2


def test_rename_to_someone_elses_name_is_refused(env):
    _, auth = env
    auth.create_guest("Ana")
    bob, _ = auth.create_guest("Bob")
    with pytest.raises(ValueError, match="err.name_taken"):
        auth.rename(bob, "ana")
    auth.rename(bob, "Bobby")


def test_sign_out_of_last_device_forgets_the_name(env):
    db, auth = env
    a, _ = auth.create_guest("Lia")
    b, _ = auth.create_guest("Lia")
    auth.logout(a)
    assert db.scalar("SELECT COUNT(*) FROM users") == 1      # still signed in elsewhere
    auth.logout(b)
    assert db.scalar("SELECT COUNT(*) FROM users") == 0


def test_idle_people_and_devices_expire_but_queued_tracks_protect_them(env):
    db, auth = env
    old, _ = auth.create_guest("Old")
    busy, _ = auth.create_guest("Busy")
    fresh, _ = auth.create_guest("Fresh")
    db.execute("INSERT INTO queue(track_key, track, user_id, pos, added_at) VALUES ('a', '{}', ?, 1, 0)", (busy.user_id,))
    db.execute("INSERT INTO playlists(name, owner_id, shared, created_at) VALUES ('mine', ?, 0, 0)", (old.user_id,))
    db.execute("INSERT INTO playlists(name, owner_id, shared, created_at) VALUES ('office', ?, 1, 0)", (old.user_id,))
    age(db, old.user_id, 40)
    age(db, busy.user_id, 40)
    r = cleanup(db, guest_days=30, history_days=30)
    names = {row["name"] for row in db.all("SELECT name FROM users")}
    assert names == {"Busy", "Fresh"} and r["users"] == 1
    assert [p["name"] for p in db.all("SELECT name FROM playlists")] == ["office"]   # shared ones stay
    assert db.scalar("SELECT COUNT(*) FROM sessions WHERE user_id=?", (busy.user_id,)) == 0


def test_history_ages_out(env):
    db, _ = env
    now = time.time()
    db.execute("INSERT INTO history(track_key, track, started_at) VALUES ('x', '{}', ?)", (now - 45 * DAY,))
    db.execute("INSERT INTO history(track_key, track, started_at) VALUES ('y', '{}', ?)", (now - DAY,))
    cleanup(db, guest_days=30, history_days=30, vacuum=True)
    assert [r["track_key"] for r in db.all("SELECT track_key FROM history")] == ["y"]


def test_old_duplicate_guests_are_merged(env):
    db, _ = env
    for t in (100, 200):
        db.execute("INSERT INTO users(name, kind, created_at, last_seen) VALUES ('Juan', 'guest', 0, ?)", (t,))
    db.execute("INSERT INTO queue(track_key, track, user_id, pos, added_at) VALUES ('a', '{}', 1, 1, 0)")
    assert merge_duplicate_guests(db) == 1
    assert db.scalar("SELECT COUNT(*) FROM users") == 1
    assert db.scalar("SELECT user_id FROM queue") == 2      # moved to the most recently seen one
