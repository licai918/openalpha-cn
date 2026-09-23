"""The one import of `importlinter.cli` in this tree, and the containment every caller goes through.

`importlinter.cli.lint_imports` calls `importlinter.cli._configure_logging`, which calls
`logging.config.dictConfig` with a config naming only `importlinter`, `grimp` and `_rustgrimp`.
`dictConfig` defaults to `disable_existing_loggers=True`, and that default sets `.disabled = True`
on **every** logger that already exists and is not named in (or a child of) the config -- including
`openalpha_cn.storage.migrations` and `openalpha_cn.storage.batch`, which
`tests/integration/storage/test_migrations.py` and `tests/integration/test_batch_research.py`
create at collection time, long before any test that runs the linter.

A disabled logger emits nothing, so `caplog` captures nothing, so a `caplog` acceptance three
directories away fails on `assert 0 == 1` with no mention of the import linter anywhere in it.
Neither the root logger nor any parent's level, propagate flag or handler list changes -- which is
why the first probe for this missed it -- and `logging.disable` is untouched too. The mutated state
is the per-logger `disabled` attribute, and nothing in `logging` restores it.

The acceptances measured going red over the life of this defect, carried here because the three
private copies of the wrapper that recorded them are gone:

- `tests/integration/storage/test_migrations.py::
  test_run_migrations_logs_the_backup_path_and_each_applied_migration`
- `tests/integration/storage/test_migrations.py::
  test_run_migrations_logs_failure_without_leaking_the_underlying_exception_message`
- `tests/integration/test_batch_research.py`'s four `caplog` acceptances
- `tests/unit/runtime/test_composition_migrations.py::
  test_build_storage_logs_runtime_dir_and_schema_version_on_startup`
- `tests/unit/test_cli.py::
  test_probe_report_logs_provider_failure_category_and_provider_id_not_the_message`

Not one of them mentions the import linter.

## Why this is a module of its own, against the argument that said it should not be

`tests/unit/test_import_layering.py` wrote the wrapper first, for itself.
`tests/unit/backtest/test_candidate_ranking.py` then needed one and **copied** it, recording the
reason in its own docstring: importing it would make "one collected test module the import-time
dependency of another and give pytest two paths to the same file", and the duplication was only
eight lines because each file also carried a private
`test_no_test_in_this_module_calls_lint_imports_without_restoring_logging` -- a `re.findall` over
**its own source** for a bare ``lint_imports(``. `tests/unit/backtest/test_shortlist_gate.py`
became the third copy.

That objection was about importing from a **collected** module, and it was right about that. It
is not an argument against a plain module at the `tests/` root, which is what
`tests/offline_guard.py` already is and for a closely related reason -- pytest imports each
`conftest.py` under its own basename, so `import conftest` is ambiguous the moment a second one
exists. This file is not collected, is importable from every directory in the tree (no `__init__
.py` anywhere, and `tests/conftest.py` puts `tests/` on `sys.path`), and gives the rule one home.

**The copy-plus-private-guard convention failed twice.** `V2-P4-068` filed the first
reintroduction and `V2-P4-012` diagnosed it; `V2-P4-089` measured the second, and how it arrived
is the whole argument for this file: `tests/unit/test_model_view.py` imported the raw CLI under
the alias `_lint_imports` -- the exact name of `test_import_layering.py`'s wrapper -- so the file
read as if it were contained, and every private regex was keyed on a spelling that alias did not
have. Six guards went hollow and the suite stayed green only because pytest's default collection
order happens to put `tests/integration` before `tests/unit`. Measured at `fadf72d`::

    pytest tests/unit/test_model_view.py tests/integration/storage/test_migrations.py \\
           tests/integration/test_batch_research.py -q
    -> 6 failed, 46 passed          (all six: assert 0 == 1)
    (the same three paths, integration first)                    -> 52 passed

## The third instance, which was in the guard itself

Sweeping for the import rather than the call found a fourth file, and then something worse: the
one test that *proves* the pollution is real ran the raw CLI and put back **the single logger it
named**, leaving every other one disabled. Measured with `test_model_view.py` already routed
through the containment, so nothing from that file is involved -- and on a code path `fadf72d`
had character for character::

    pytest tests/unit/test_import_layering.py tests/integration/storage/test_migrations.py \\
           tests/integration/test_batch_research.py -q
    -> 4 failed, 54 passed

Four of the six `V2-P4-089` reported therefore had **two** causes and would have stayed red after
the fix as filed. `openalpha_cn.storage.migrations` was not among them only because it is the one
logger that test re-enabled by hand.

So the proof was not exportable as "one file may call the raw CLI": a permission slip would have
carried the same defect wherever it went. `raw_lint_imports_disables` is that proof turned into an
operation this module owns -- it runs the raw CLI, reports what the caller needs to *see*, and
restores everything on the way out -- and the raw function is exported nowhere. Both public
functions here restore the whole snapshot, so there is no call anybody can make through this
module that leaves logging damaged, and no list of who is allowed to make it.

`tests/unit/test_import_layering.py::
test_the_import_linter_cli_is_reached_from_one_file_in_this_whole_tree` is what keeps this the
only import, and it reads `import` statements off the AST of every file under `tests/` rather
than a call spelling out of one.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from importlinter.cli import lint_imports as _lint_imports_cli


@contextmanager
def restored_logger_flags() -> Iterator[None]:
    """Put every existing logger's `disabled` flag back the way it was found.

    In **both** directions: a logger that was enabled is enabled again, and one that was already
    disabled stays disabled. Blanket-enabling everything reachable would be a different and wrong
    repair -- it would turn a test that deliberately silenced a logger into one that does not.

    Loggers `dictConfig` *creates* (`importlinter`, `grimp`, `_rustgrimp`) are not in the snapshot
    and are left as it configured them, which is the correct asymmetry: this restores state that
    existed, and inventing a prior state for a logger that did not exist would be a guess.
    """
    manager = logging.Logger.manager
    before = {
        name: existing.disabled
        for name, existing in manager.loggerDict.items()
        if isinstance(existing, logging.Logger)
    }
    try:
        yield
    finally:
        for name, disabled in before.items():
            restored = manager.loggerDict.get(name)
            if isinstance(restored, logging.Logger):
                restored.disabled = disabled


def contained_lint_imports(**kwargs: object) -> int:
    """`importlinter.cli.lint_imports`, with the logging state it silently wrecks put back.

    The name says `contained` at every call site on purpose. The previous arrangement had each
    file alias its own wrapper to a private name, which is what let a raw import wearing that same
    name pass three reviews.
    """
    with restored_logger_flags():
        return _lint_imports_cli(**kwargs)  # type: ignore[arg-type]


def raw_lint_imports_disables(logger_name: str, **kwargs: object) -> bool:
    """Whether the **raw** CLI leaves `logger_name` disabled -- observed, then undone.

    The one thing `contained_lint_imports` cannot show, because its whole job is that nobody sees
    it: that `dictConfig(disable_existing_loggers=True)` is still the mechanism. Without this,
    `restored_logger_flags` is a `finally` block no test can say is still needed, and the first
    person to find it in the way deletes it.

    The observation is taken inside the restore rather than left for the caller to take after it,
    which is the correction `V2-P4-089`'s sweep forced: the test that used to do this by hand
    re-enabled the single logger it asserted about and left the rest of the process disabled.
    `logger_name` is created if it does not already exist, so the answer is about this call rather
    than about whether some other module was imported first.
    """
    logging.getLogger(logger_name)
    with restored_logger_flags():
        _lint_imports_cli(**kwargs)  # type: ignore[arg-type]
        return logging.getLogger(logger_name).disabled
