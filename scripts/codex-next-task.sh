#!/usr/bin/env bash

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

echo "MintFlow autonomous task runner"
echo

BRANCH="$(git branch --show-current)"

if [[ "$BRANCH" == "main" ]]; then
    echo "ERROR: Refusing to run autonomous development on main."
    exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "ERROR: Working tree contains tracked uncommitted changes."
    echo "Review and commit the current task before starting another one."
    exit 1
fi

UNTRACKED="$(git ls-files --others --exclude-standard)"

if [[ -n "$UNTRACKED" ]]; then
    echo "ERROR: Working tree contains untracked files:"
    echo "$UNTRACKED"
    exit 1
fi

TASK="$(
python - <<'PY'
from pathlib import Path

tasks_dir = Path("docs/tasks")

for path in sorted(tasks_dir.glob("AUTH-*.md")):
    lines = path.read_text(encoding="utf-8").splitlines()

    status = next(
        (
            line.removeprefix("Status:").strip()
            for line in lines
            if line.startswith("Status:")
        ),
        None,
    )

    if status == "ready":
        print(path)
        break
PY
)"

if [[ -z "${TASK:-}" ]]; then
    echo "No ready tasks."
    exit 0
fi

echo "Branch: $BRANCH"
echo "Task:   $TASK"
echo

PROMPT=$(cat <<EOF_PROMPT
Read AGENTS.md first.

Execute exactly this implementation task:

$TASK

Work autonomously until either:

1. every acceptance criterion is complete and every required check passes, or
2. a material architecture/product/security/dependency decision requires human approval.

Rules:

- stay strictly inside task scope;
- read every document referenced by the task;
- inspect existing code before editing;
- use safe local development commands autonomously;
- run focused tests;
- run PostgreSQL integration tests where required;
- run the complete project quality gates;
- fix valid failures;
- perform a final complete-diff self-review;
- fix valid review findings;
- re-run affected checks.

Never:

- commit;
- push;
- merge;
- rebase;
- switch branches;
- create or delete branches;
- create tags;
- deploy;
- modify secrets;
- modify historical migrations;
- broaden product scope;
- introduce unapproved external or paid services.

If successful:

- change the task's top-level status from ready to review;
- provide only a concise completion report;
- stop.

If blocked:

- leave the task status unchanged;
- make no speculative workaround;
- clearly report the human decision required;
- stop.
EOF_PROMPT
)

codex exec "$PROMPT"
