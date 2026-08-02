import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APPROVE_TASK = PROJECT_ROOT / "scripts" / "approve-task.sh"


def write_task(repository: Path, task_id: str, status: str) -> None:
    task_path = repository / "docs" / "tasks" / f"{task_id}.md"
    task_path.write_text(
        f"# {task_id} — Test task\n\nStatus: {status}\n",
        encoding="utf-8",
    )


def initialize_repository(
    tmp_path: Path,
    *,
    dependent_human_approval: str | None = None,
) -> Path:
    repository = tmp_path / "repository"
    tasks_dir = repository / "docs" / "tasks"
    scripts_dir = repository / "scripts"
    tasks_dir.mkdir(parents=True)
    scripts_dir.mkdir()
    shutil.copy(APPROVE_TASK, scripts_dir / "approve-task.sh")

    human_approval = (
        f"    human_approval: {dependent_human_approval}\n"
        if dependent_human_approval is not None
        else ""
    )
    (tasks_dir / "index.yaml").write_text(
        "sprint: test\n\n"
        "tasks:\n"
        "  - id: AUTH-01\n"
        "    status: review\n"
        "    depends_on: []\n\n"
        "  - id: AUTH-02\n"
        "    status: blocked\n"
        f"{human_approval}"
        "    depends_on:\n"
        "      - AUTH-01\n",
        encoding="utf-8",
    )
    write_task(repository, "AUTH-01", "review")
    write_task(repository, "AUTH-02", "blocked")

    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=repository,
        check=True,
    )
    return repository


def run_approval(repository: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/approve-task.sh", "AUTH-01"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def run_failed_approval(repository: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/approve-task.sh", "AUTH-01"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )


def task_status(repository: Path, task_id: str) -> str:
    task = repository / "docs" / "tasks" / f"{task_id}.md"
    return next(
        line.removeprefix("Status:").strip()
        for line in task.read_text(encoding="utf-8").splitlines()
        if line.startswith("Status:")
    )


def test_dependency_only_task_becomes_ready(tmp_path: Path) -> None:
    repository = initialize_repository(tmp_path)

    result = run_approval(repository)

    assert task_status(repository, "AUTH-02") == "ready"
    assert "AUTH-02 -> ready" in result.stdout


def test_pending_human_approval_keeps_task_blocked(
    tmp_path: Path,
) -> None:
    repository = initialize_repository(
        tmp_path,
        dependent_human_approval="pending",
    )

    result = run_approval(repository)

    assert task_status(repository, "AUTH-02") == "blocked"
    assert "Still blocked by unresolved human approval:" in result.stdout
    assert "AUTH-02 -> blocked" in result.stdout


def test_invalid_human_approval_does_not_modify_files(tmp_path: Path) -> None:
    repository = initialize_repository(
        tmp_path,
        dependent_human_approval="invalid",
    )
    index_path = repository / "docs" / "tasks" / "index.yaml"
    index_before = index_path.read_bytes()

    result = run_failed_approval(repository)

    assert result.returncode != 0
    assert "invalid human_approval value 'invalid'" in result.stderr
    assert task_status(repository, "AUTH-01") == "review"
    assert index_path.read_bytes() == index_before
