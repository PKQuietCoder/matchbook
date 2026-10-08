"""Shared fixtures. Nothing here touches the committed store or needs a key."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from process import csvio, store  # noqa: E402
from process.log import EventLogBuilder  # noqa: E402


def moment(day: int, hour: int = 9, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 1, day, hour, minute, second, tzinfo=timezone.utc)


@pytest.fixture()
def tiny_log():
    """Five hand-built cases with a known shape, so expectations are exact.

    c1  a clean 3-way-match item
    c2  clears before the goods receipt           -> CTRL-GR
    c3  one actor does GR and clearing            -> CTRL-SOD
    c4  removes a block that was never set        -> CTRL-BLOCK
    c5  a 2-way-match item with no GR at all      -> no violation, by design
    """
    builder = EventLogBuilder("tiny", activity_rank={"Record Goods Receipt": 100, "Clear Invoice": 130})

    def case(case_id, flow, steps):
        builder.add_case_attributes(case_id, {"Item Category": flow})
        for day, activity, resource, value in steps:
            builder.add(
                case_id=case_id,
                activity=activity,
                timestamp=moment(day),
                resource=resource,
                value_cents=value,
            )

    three_way = "3-way match, invoice after GR"
    case("c1", three_way, [
        (1, "Create Purchase Order Item", "user_1", 100_00),
        (2, "Record Goods Receipt", "user_2", 100_00),
        (3, "Record Invoice Receipt", "user_3", 100_00),
        (4, "Clear Invoice", "user_4", 100_00),
    ])
    case("c2", three_way, [
        (1, "Create Purchase Order Item", "user_1", 100_00),
        (2, "Record Invoice Receipt", "user_3", 100_00),
        (3, "Clear Invoice", "user_4", 100_00),
        (4, "Record Goods Receipt", "user_2", 100_00),
    ])
    case("c3", three_way, [
        (1, "Create Purchase Order Item", "user_1", 100_00),
        (2, "Record Goods Receipt", "user_9", 100_00),
        (3, "Record Invoice Receipt", "user_3", 100_00),
        (4, "Clear Invoice", "user_9", 100_00),
    ])
    case("c4", three_way, [
        (1, "Create Purchase Order Item", "user_1", 100_00),
        (2, "Record Goods Receipt", "user_2", 100_00),
        (3, "Record Invoice Receipt", "user_3", 100_00),
        (4, "Remove Payment Block", "user_5", 100_00),
        (5, "Clear Invoice", "user_4", 100_00),
    ])
    case("c5", "2-way match", [
        (1, "Create Purchase Order Item", "user_1", 50_00),
        (2, "Record Invoice Receipt", "user_3", 50_00),
        (3, "Clear Invoice", "user_4", 50_00),
    ])
    return builder.build()


@pytest.fixture()
def tiny_store(tiny_log, tmp_path):
    path = tmp_path / "eventstore.db"
    store.save(tiny_log, path)
    return path


@pytest.fixture(scope="session")
def helpdesk_log():
    """The committed MIT fixture. Small enough to load per session."""
    return csvio.read_csv(
        REPO_ROOT / "logs" / "helpdesk" / "helpdesk.csv",
        log_id="helpdesk",
        columns=csvio.HELPDESK_COLUMNS,
        license="MIT",
    )


@pytest.fixture(scope="session")
def snapshot_log():
    """The committed BPI 2019 sample: real data, in the test suite."""
    log = csvio.read_csv(
        REPO_ROOT / "logs" / "snapshot" / "bpic19-sample-events.csv.gz",
        log_id="bpic19-sample",
        license="CC BY 4.0",
    )
    csvio.read_case_attributes(
        log, REPO_ROOT / "logs" / "snapshot" / "bpic19-sample-cases.csv.gz"
    )
    return log


@pytest.fixture()
def world(tmp_path_factory):
    """A seeded world in a temp directory. Tests never touch data/."""
    import os

    from seed.generate import generate_world

    root = tmp_path_factory.mktemp("world")
    db = root / "matchbook.db"
    policies = root / "policies"
    generate_world(db_path=db, policies_dir=policies, quiet=True)
    previous = {key: os.environ.get(key) for key in ("MB_DB", "MB_POLICIES_DIR")}
    os.environ["MB_DB"] = str(db)
    os.environ["MB_POLICIES_DIR"] = str(policies)
    yield {"db": db, "policies": policies}
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture()
def world_copy(world, tmp_path):
    """A per-test copy, for tests that write. Re-seeding is the sandbox reset."""
    import os
    import shutil

    copy = tmp_path / "matchbook.db"
    shutil.copy(world["db"], copy)
    os.environ["MB_DB"] = str(copy)
    yield copy
    os.environ["MB_DB"] = str(world["db"])


@pytest.fixture(autouse=True)
def killswitch_off(monkeypatch):
    monkeypatch.delenv("MB_KILL_SWITCH", raising=False)
