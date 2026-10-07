"""Optional build-time commit stamp, consumed by `runtime.provenance.resolve_code_commit`.

`BUILD_COMMIT` ships as `None` in every checkout and in any wheel built without
regenerating this file. A release build that still has `.git` available (typically CI,
immediately before `uv build`) can overwrite this module's constant with the exact
commit being packaged -- so a machine that installs the resulting wheel with **no**
`.git` present at all (the scenario `resolve_code_commit()`'s test suite exercises,
`tests/unit/runtime/test_provenance.py`) still reports a real commit instead of falling
all the way through to the explicit `UNKNOWN_CODE_COMMIT` marker.

No such regeneration step exists in this repository's build tooling yet -- there is no
CI packaging pipeline to hook it into (the current Docker build runs `uv sync --locked
--no-dev --no-editable` straight from a git checkout inside the image, never `uv
build`, so it always lands on the git-workspace tier instead). `resolve_code_commit()`
still fully implements and tests this tier now, so wiring in the one missing step later
-- writing this constant immediately before `uv build` -- is a build-script change, not
a design change.
"""

BUILD_COMMIT: str | None = None
