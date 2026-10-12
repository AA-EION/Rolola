"""Queue ordering, fairness, limits and vote-skip thresholds."""

import pytest

from rolola.db import Database
from rolola.models import Track
from rolola.queue import QueueError, QueueItem, QueueManager, order_items
from rolola.votes import VoteBox, required_votes


def item(i, user, pos=None, pinned=False, added=None):
    return QueueItem(i, Track(source="local", id=str(i), title=f"t{i}"), user, pos if pos is not None else i,
                     pinned, added if added is not None else i)


# --- pure ordering -----------------------------------------------------------
def test_fairness_off_is_fifo():
    items = [item(1, "a"), item(2, "a"), item(3, "b")]
    assert [i.id for i in order_items(items, fairness=False)] == [1, 2, 3]


def test_round_robin_interleaves_users():
    # Alice floods the queue, then Bob and Carol add one each.
    items = [item(i, "alice") for i in range(1, 6)] + [item(6, "bob"), item(7, "carol")]
    order = [i.user_id for i in order_items(items, fairness=True)]
    assert order == ["alice", "bob", "carol", "alice", "alice", "alice", "alice"]


def test_least_recently_served_goes_first():
    items = [item(1, "alice"), item(2, "bob")]
    order = order_items(items, True, last_served={"alice": 100.0, "bob": 50.0})
    assert [i.user_id for i in order] == ["bob", "alice"]


def test_pinned_items_jump_the_line():
    items = [item(1, "a"), item(2, "b"), item(3, "c", pos=-1, pinned=True)]
    assert order_items(items, True)[0].id == 3
    assert order_items(items, False)[0].id == 3


# --- manager (real SQLite) ------------------------------------------------------
@pytest.fixture
def qm(tmp_path):
    db = Database(tmp_path / "t.db")
    for uid in (1, 2, 3):
        db.execute("INSERT INTO users(id, name, created_at, last_seen) VALUES (?,?,0,0)", (uid, f"u{uid}"))
    settings = {"fairness_enabled": True, "max_queued_per_user": 3}
    changes = []
    m = QueueManager(db, settings.get, lambda: changes.append(1))
    m.settings = settings
    m.changes = changes
    return m


def tracks(n, prefix="t"):
    return [Track(source="local", id=f"{prefix}{i}", title=f"{prefix}{i}") for i in range(n)]


def test_per_user_limit_truncates_and_then_refuses(qm):
    added, dropped = qm.add(tracks(5), user_id=1)
    assert len(added) == 3 and dropped == 2
    with pytest.raises(QueueError) as e:
        qm.add(tracks(1, "x"), user_id=1)
    assert e.value.code == "err.queue_limit"
    # admins are exempt
    added, dropped = qm.add(tracks(2, "adm"), user_id=1, is_admin=True)
    assert len(added) == 2 and dropped == 0


def test_limit_zero_means_unlimited(qm):
    qm.settings["max_queued_per_user"] = 0
    added, _ = qm.add(tracks(20), user_id=1)
    assert len(added) == 20


def test_users_only_remove_their_own(qm):
    (a,), _ = qm.add(tracks(1, "a"), user_id=1)
    with pytest.raises(QueueError):
        qm.remove(a.id, user_id=2, is_admin=False)
    qm.remove(a.id, user_id=2, is_admin=True)
    assert len(qm) == 0


def test_manager_interleaves_and_serves(qm):
    qm.add(tracks(3, "a"), user_id=1)
    qm.add(tracks(1, "b"), user_id=2)
    assert [i.user_id for i in qm.ordered()] == [1, 2, 1, 1]
    head = qm.head()
    qm.take(head.id)
    qm.mark_served(1)
    # user 1 was just served, so user 2 goes next
    assert qm.head().user_id == 2


def test_user_reorders_only_within_own_items(qm):
    a, _ = qm.add(tracks(3, "a"), user_id=1)
    qm.add(tracks(1, "b"), user_id=2)
    qm.move(a[2].id, "top", user_id=1, is_admin=False)
    mine = [i.track.id for i in qm.ordered() if i.user_id == 1]
    assert mine == ["a2", "a0", "a1"]
    # still interleaved: user 1's first slot, then user 2
    assert [i.user_id for i in qm.ordered()][:2] == [1, 2]
    b = [i for i in qm.ordered() if i.user_id == 2][0]
    with pytest.raises(QueueError):
        qm.move(b.id, "up", user_id=1, is_admin=False)


def test_admin_top_pins(qm):
    qm.add(tracks(2, "a"), user_id=1)
    (b,), _ = qm.add(tracks(1, "b"), user_id=2)
    qm.move(b.id, "top", user_id=3, is_admin=True)
    assert qm.head().id == b.id and qm.head().pinned


def test_admin_moves_globally_without_fairness(qm):
    qm.settings["fairness_enabled"] = False
    a, _ = qm.add(tracks(2, "a"), user_id=1)
    (b,), _ = qm.add(tracks(1, "b"), user_id=2)
    qm.move(b.id, "up", user_id=3, is_admin=True)
    assert [i.track.id for i in qm.ordered()] == ["a0", "b0", "a1"]


# --- votes ----------------------------------------------------------------------
@pytest.mark.parametrize("listeners,mode,value,expected", [
    (10, "percent", 50, 5),
    (9, "percent", 50, 5),
    (1, "percent", 50, 1),
    (0, "percent", 50, 1),
    (3, "percent", 100, 3),
    (10, "count", 3, 3),
    (2, "count", 3, 2),      # capped by listeners
    (5, "count", 0, 1),      # never zero
])
def test_required_votes(listeners, mode, value, expected):
    assert required_votes(listeners, mode, value) == expected


def test_votebox_dedup_and_reset():
    box = VoteBox()
    box.reset(1)
    assert box.vote(10) and not box.vote(10)
    box.vote(11)
    assert box.passed(4, "percent", 50)
    assert not box.passed(5, "percent", 50)
    box.reset(2)
    assert box.voters == set() and box.play_id == 2
