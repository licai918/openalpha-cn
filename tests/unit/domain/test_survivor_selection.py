"""The selection layer's default rule (`V2-P7-005`).

`seeded_hash/v1` claims no skill; what it owes is determinism. The order must be a function of the
candidate *set* and the seed, so every property below is a way of saying "and of nothing else":
not the order the candidates arrived in, not duplicates, not the day, and -- the one a first
prototype got wrong -- not `PYTHONHASHSEED`.
"""

import json
import os
import random
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from openalpha_cn.domain.survivor_selection import SeededHashRule, selection_rule

CODES = tuple(f"{600000 + position:06d}.SH" for position in range(40))
SOURCE_ROOT = str(Path(__file__).resolve().parents[3] / "src")
"""The tree under test, handed to the child processes so they import it rather than whichever
copy the interpreter's own path happens to find first (an isolated worktree, say)."""


def test_the_order_is_each_distinct_candidate_once() -> None:
    drawn = SeededHashRule().order([*CODES, CODES[3], CODES[3]], seed=7, session=3)

    assert sorted(drawn) == sorted(CODES)
    assert len(drawn) == len(CODES)


def test_the_order_is_ascending_in_the_seeded_hash_and_nothing_else() -> None:
    drawn = SeededHashRule().order(CODES, seed=7, session=3)
    keys = [sha256(f"7|3|{code}".encode()).hexdigest() for code in drawn]

    assert keys == sorted(keys)


def test_the_order_does_not_depend_on_the_order_the_candidates_arrive_in() -> None:
    reference = SeededHashRule().order(CODES, seed=7, session=3)
    for shuffle_seed in range(10):
        shuffled = list(CODES)
        random.Random(shuffle_seed).shuffle(shuffled)

        assert SeededHashRule().order(shuffled, seed=7, session=3) == reference


def test_a_set_of_candidates_draws_the_same_as_the_sorted_list() -> None:
    assert SeededHashRule().order(set(CODES), seed=7, session=3) == SeededHashRule().order(
        sorted(CODES), seed=7, session=3
    )


def test_the_seed_changes_the_order() -> None:
    rule = SeededHashRule()

    assert rule.order(CODES, seed=7, session=3) != rule.order(CODES, seed=8, session=3)


def test_each_session_is_a_fresh_draw_so_one_name_does_not_head_the_queue_every_time() -> None:
    rule = SeededHashRule()

    assert rule.order(CODES, seed=7, session=3) != rule.order(CODES, seed=7, session=4)
    first_picks = {rule.order(CODES, seed=7, session=session)[0] for session in range(40)}
    # A day-independent queue would give exactly one first pick. Forty independent draws over
    # forty names give about 25 distinct ones; "more than ten" cannot fail by chance.
    assert len(first_picks) > 10


def test_the_order_is_pinned_for_a_small_fixed_input() -> None:
    """A change of canonical form (separator, encoding, algorithm) moves this, among others."""
    codes = ["000001.SZ", "000002.SZ", "600000.SH", "600519.SH", "300750.SZ"]

    assert SeededHashRule().order(codes, seed=42, session=3) == (
        "600000.SH",
        "000001.SZ",
        "300750.SZ",
        "000002.SZ",
        "600519.SH",
    )


def test_no_candidates_draw_nothing() -> None:
    assert SeededHashRule().order([], seed=7, session=3) == ()


def test_the_rule_is_looked_up_by_its_registered_name() -> None:
    assert isinstance(selection_rule("seeded_hash/v1"), SeededHashRule)
    with pytest.raises(ValueError, match=r"unknown selection rule 'nope'.*seeded_hash/v1"):
        selection_rule("nope")


def test_the_order_is_the_same_in_processes_with_different_hash_seeds() -> None:
    """The defect a prototype had: drawing from a `set` gave different numbers per process."""
    program = (
        "import json,sys;"
        "from openalpha_cn.domain.survivor_selection import SeededHashRule;"
        "codes=json.loads(sys.argv[1]);"
        "print(json.dumps(SeededHashRule().order(set(codes), seed=7, session=3)))"
    )
    answers = set()
    for hash_seed in ("0", "1", "12345"):
        completed = subprocess.run(
            [sys.executable, "-c", program, json.dumps(list(CODES))],
            env={
                **os.environ,
                "PYTHONHASHSEED": hash_seed,
                "PYTHONPATH": os.pathsep.join(
                    part for part in (SOURCE_ROOT, os.environ.get("PYTHONPATH")) if part
                ),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        answers.add(completed.stdout.strip())

    assert answers == {json.dumps(list(SeededHashRule().order(CODES, seed=7, session=3)))}
