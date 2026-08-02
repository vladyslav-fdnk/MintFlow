#!/usr/bin/env bash

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

TASK_ID="${1:-}"

if [[ -z "$TASK_ID" ]]; then
    echo "Usage: ./scripts/approve-task.sh AUTH-03"
    exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "ERROR: Working tree must be clean before approving a task."
    exit 1
fi

UNTRACKED="$(git ls-files --others --exclude-standard)"

if [[ -n "$UNTRACKED" ]]; then
    echo "ERROR: Working tree contains untracked files."
    echo "$UNTRACKED"
    exit 1
fi

uv run python - "$TASK_ID" <<'PY'
from pathlib import Path
import sys

task_id = sys.argv[1]

tasks_dir = Path("docs/tasks")
index_path = tasks_dir / "index.yaml"

task_files = sorted(tasks_dir.glob("AUTH-*.md"))

selected = None

for path in task_files:
    text = path.read_text(encoding="utf-8")
    first_heading = next(
        (line for line in text.splitlines() if line.startswith("# ")),
        "",
    )

    if task_id in first_heading:
        selected = path
        break

if selected is None:
    raise SystemExit(f"ERROR: Task {task_id} not found.")

lines = selected.read_text(encoding="utf-8").splitlines()

status_index = next(
    (
        index
        for index, line in enumerate(lines)
        if line.startswith("Status:")
    ),
    None,
)

if status_index is None:
    raise SystemExit(f"ERROR: {selected} has no top-level Status.")

current_status = lines[status_index].removeprefix("Status:").strip()

if current_status != "review":
    raise SystemExit(
        f"ERROR: {task_id} must be in review before approval; "
        f"current status is {current_status!r}."
    )

index_lines = index_path.read_text(encoding="utf-8").splitlines()

current_task = None
task_status_lines: dict[str, int] = {}
task_dependencies: dict[str, list[str]] = {}
task_human_approvals: dict[str, str] = {}

for index, line in enumerate(index_lines):
    stripped = line.strip()

    if stripped.startswith("- id:"):
        current_task = stripped.split(":", 1)[1].strip()
        task_dependencies.setdefault(current_task, [])

    elif current_task and stripped.startswith("status:"):
        task_status_lines[current_task] = index

    elif current_task and stripped.startswith("human_approval:"):
        approval = stripped.split(":", 1)[1].strip()

        if approval not in {"pending", "approved"}:
            raise SystemExit(
                f"ERROR: {current_task} has invalid human_approval "
                f"value {approval!r}; expected 'pending' or 'approved'."
            )

        task_human_approvals[current_task] = approval

    elif current_task and stripped.startswith("- AUTH-"):
        task_dependencies.setdefault(current_task, []).append(
            stripped.removeprefix("- ").strip()
        )

if task_id not in task_status_lines:
    raise SystemExit(f"ERROR: {task_id} not found in index.yaml.")

lines[status_index] = "Status: done"
selected.write_text("\n".join(lines) + "\n", encoding="utf-8")

index_lines[task_status_lines[task_id]] = "    status: done"

completed = {
    task
    for task, status_line in task_status_lines.items()
    if (
        task == task_id
        or index_lines[status_line].strip() == "status: done"
    )
}

unblocked: list[str] = []
human_blocked: list[str] = []

for task, dependencies in task_dependencies.items():
    if task == task_id:
        continue

    status_line = task_status_lines.get(task)

    if status_line is None:
        continue

    status = index_lines[status_line].split(":", 1)[1].strip()

    if status != "blocked":
        continue

    if dependencies and all(dep in completed for dep in dependencies):
        if task_human_approvals.get(task) == "pending":
            human_blocked.append(task)
            continue

        index_lines[status_line] = "    status: ready"
        unblocked.append(task)

        for path in task_files:
            text = path.read_text(encoding="utf-8")
            heading = next(
                (line for line in text.splitlines() if line.startswith("# ")),
                "",
            )

            if task not in heading:
                continue

            task_lines = text.splitlines()

            task_status_index = next(
                (
                    i
                    for i, line in enumerate(task_lines)
                    if line.startswith("Status:")
                ),
                None,
            )

            if task_status_index is not None:
                task_lines[task_status_index] = "Status: ready"
                path.write_text(
                    "\n".join(task_lines) + "\n",
                    encoding="utf-8",
                )

            break

index_path.write_text(
    "\n".join(index_lines) + "\n",
    encoding="utf-8",
)

print(f"Approved: {task_id} -> done")

if unblocked:
    print("Unblocked:")
    for task in unblocked:
        print(f"  {task} -> ready")
else:
    print("No dependent tasks unblocked.")

if human_blocked:
    print("Still blocked by unresolved human approval:")
    for task in human_blocked:
        print(f"  {task} -> blocked")
PY
