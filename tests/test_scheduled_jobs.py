"""Scheduled jobs and their dead man's switches (docs/operations_design.md, O5, O8)."""

import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


# --- the schedule -------------------------------------------------------------------------------


def _jobs() -> list[tuple[str, str, str]]:
    """(minute hour day month weekday, check name, command) for every job in the crontab."""
    jobs = []
    for line in (DEPLOY / "crontab").read_text().splitlines():
        if not line.strip() or line.startswith("#") or "=" in line.split()[0]:
            continue
        fields = line.split()
        assert fields[5] == "$M/run-job.sh", line
        jobs.append((" ".join(fields[:5]), fields[6], " ".join(fields[7:])))
    return jobs


def test_every_job_runs_on_the_documented_schedule() -> None:
    assert sorted((schedule, name) for schedule, name, _ in _jobs()) == sorted(
        [
            ("30 2 * * *", "BACKUP"),
            ("10 3 * * *", "AUTH_CLEANUP"),
            ("20 3 * * *", "TELEGRAM_CLEANUP"),
            ("0 6 * * *", "EXCHANGE_RATES"),
            ("30 15 * * *", "EXCHANGE_RATES"),
            ("0 8 * * *", "DISK"),
        ]
    )


def test_each_job_runs_the_right_command_and_logs_to_the_journal() -> None:
    commands = {name: command for _, name, command in _jobs()}

    assert commands["BACKUP"].startswith("$M/backup.sh")
    assert commands["AUTH_CLEANUP"].startswith("$M/mintflow-command.sh authentication_retention")
    assert commands["TELEGRAM_CLEANUP"].startswith("$M/mintflow-command.sh telegram_retention")
    assert commands["EXCHANGE_RATES"].startswith("$M/mintflow-command.sh refresh_exchange_rates")
    assert commands["DISK"].startswith("$M/check-disk.sh")
    assert all(command.endswith("2>&1 | logger -t mintflow") for command in commands.values())


def test_every_check_has_a_ping_url_in_the_example() -> None:
    example = (DEPLOY / "healthchecks.env.example").read_text()

    assert {name for _, name, _ in _jobs()} == {
        line.split("=")[0].removeprefix("HEALTHCHECK_")
        for line in example.splitlines()
        if line.startswith("HEALTHCHECK_")
    }


def test_every_command_the_crontab_names_exists() -> None:
    modules = Path(__file__).resolve().parents[1] / "src" / "mintflow" / "commands"
    for _, _, command in _jobs():
        words = command.split()
        if words[0] == "$M/mintflow-command.sh":
            assert (modules / f"{words[1]}.py").is_file(), words[1]


# --- ping only on success -----------------------------------------------------------------------


class _Pings(BaseHTTPRequestHandler):
    received: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 (the http.server API)
        _Pings.received.append(self.path)
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return None


@pytest.fixture
def ping_server() -> Iterator[str]:
    _Pings.received = []
    server = HTTPServer(("127.0.0.1", 0), _Pings)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/ping/secret-check-uuid"
    server.shutdown()


def _run_job(tmp_path: Path, *command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(DEPLOY / "run-job.sh"), "TEST", *command],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "MINTFLOW_DIR": str(tmp_path)},
    )


def test_a_successful_job_pings_its_check_without_printing_the_url(
    tmp_path: Path, ping_server: str
) -> None:
    (tmp_path / "healthchecks.env").write_text(f"HEALTHCHECK_TEST={ping_server}\n")

    completed = _run_job(tmp_path, "true")

    assert completed.returncode == 0
    assert _Pings.received == ["/ping/secret-check-uuid"]
    assert "secret-check-uuid" not in completed.stdout + completed.stderr


def test_a_failing_job_never_pings_and_keeps_its_exit_status(
    tmp_path: Path, ping_server: str
) -> None:
    (tmp_path / "healthchecks.env").write_text(f"HEALTHCHECK_TEST={ping_server}\n")

    completed = _run_job(tmp_path, "sh", "-c", "exit 3")

    assert completed.returncode == 3
    assert _Pings.received == []
    assert "no ping sent" in completed.stderr


def test_a_job_without_a_check_still_runs_and_says_so(tmp_path: Path) -> None:
    completed = _run_job(tmp_path, "true")

    assert completed.returncode == 0
    assert "HEALTHCHECK_TEST is not set" in completed.stderr


def test_an_unreachable_check_does_not_fail_the_job(tmp_path: Path) -> None:
    (tmp_path / "healthchecks.env").write_text("HEALTHCHECK_TEST=http://127.0.0.1:9/ping\n")

    completed = _run_job(tmp_path, "true")

    assert completed.returncode == 0
    assert "could not be delivered" in completed.stderr
    assert "127.0.0.1:9" not in completed.stdout + completed.stderr


# --- the disk check -----------------------------------------------------------------------------


@pytest.mark.parametrize(("limit", "status"), [("101", 0), ("1", 1)])
def test_the_disk_check_succeeds_only_below_its_limit(limit: str, status: int) -> None:
    completed = subprocess.run(
        [str(DEPLOY / "check-disk.sh")],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "MINTFLOW_DISK_LIMIT_PERCENT": limit},
    )

    assert completed.returncode == status
    assert "% used" in completed.stdout + completed.stderr
