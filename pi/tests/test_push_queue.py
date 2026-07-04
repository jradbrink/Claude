from pathlib import Path

from blackbox.notify import PushQueue


def msg(n: int) -> dict:
    return {"title": f"t{n}", "body": f"b{n}", "priority": "default", "tags": "x"}


def test_drain_sends_in_order_and_empties(tmp_path: Path):
    q = PushQueue(tmp_path / "q.json")
    q.add(msg(1))
    q.add(msg(2))
    sent = []
    assert q.drain(lambda m: sent.append(m) or True) == 2
    assert [m["title"] for m in sent] == ["t1", "t2"]
    assert q.drain(lambda m: True) == 0  # empty now


def test_failure_keeps_messages_for_next_drain(tmp_path: Path):
    q = PushQueue(tmp_path / "q.json")
    q.add(msg(1))
    q.add(msg(2))
    assert q.drain(lambda m: False) == 0  # offline — nothing sent

    # Simulate a power cut: a new queue instance on the same file.
    q2 = PushQueue(tmp_path / "q.json")
    sent = []
    assert q2.drain(lambda m: sent.append(m) or True) == 2
    assert [m["title"] for m in sent] == ["t1", "t2"]


def test_partial_failure_stops_at_first_unsent(tmp_path: Path):
    q = PushQueue(tmp_path / "q.json")
    for n in (1, 2, 3):
        q.add(msg(n))
    calls = []

    def send_first_only(m: dict) -> bool:
        calls.append(m["title"])
        return m["title"] == "t1"

    assert q.drain(send_first_only) == 1
    assert calls == ["t1", "t2"]  # stopped after t2 failed
    sent = []
    q.drain(lambda m: sent.append(m) or True)
    assert [m["title"] for m in sent] == ["t2", "t3"]


def test_corrupt_queue_file_is_reset(tmp_path: Path):
    path = tmp_path / "q.json"
    path.write_text("{not json")
    q = PushQueue(path)
    q.add(msg(1))
    sent = []
    assert q.drain(lambda m: sent.append(m) or True) == 1
