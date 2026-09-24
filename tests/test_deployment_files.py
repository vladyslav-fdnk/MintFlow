"""The production deployment files keep their guarantees (docs/operations_design.md, O1, O2, O8).

Compose itself resolves the file (anchors, variables, the example environment), so the checks see
exactly what the server will run.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ENV = ROOT / "deploy" / "production.env.example"
IMAGE = "ghcr.io/vladyslav-fdnk/mintflow:0000000000000000000000000000000000000000"


def _resolved_compose() -> dict[str, Any]:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "deploy" / "compose.production.yaml"),
            "--env-file",
            str(EXAMPLE_ENV),
            "--profile",
            "tools",
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "MINTFLOW_ENV_FILE": str(EXAMPLE_ENV)},
    )
    if completed.returncode != 0 and "docker compose" in completed.stderr.lower():
        pytest.skip("docker compose is not available")
    assert completed.returncode == 0, completed.stderr
    return dict(json.loads(completed.stdout))


@pytest.fixture(scope="module")
def services() -> dict[str, Any]:
    return dict(_resolved_compose()["services"])


def test_only_caddy_is_reachable_from_outside(services: dict[str, Any]) -> None:
    published = {
        name: sorted(f"{port['published']}/{port['protocol']}" for port in service.get("ports", []))
        for name, service in services.items()
    }

    assert published.pop("caddy") == ["443/tcp", "443/udp", "80/tcp"]
    assert all(ports == [] for ports in published.values()), published


def test_every_service_rotates_its_logs(services: dict[str, Any]) -> None:
    for name, service in services.items():
        logging = service["logging"]
        assert logging["driver"] == "json-file", name
        assert logging["options"] == {"max-size": "10m", "max-file": "5"}, name


def test_the_app_trusts_forwarding_only_from_the_internal_network(
    services: dict[str, Any],
) -> None:
    subnet = _resolved_compose()["networks"]["internal"]["ipam"]["config"][0]["subnet"]

    for name in ("app", "worker", "migrate"):
        environment = services[name]["environment"]
        assert environment["MINTFLOW_TRUSTED_PROXIES"] == f'["{subnet}"]', name
        assert environment["MINTFLOW_ENVIRONMENT"] == "production", name


def test_app_worker_and_migrate_run_the_same_image_tag(services: dict[str, Any]) -> None:
    assert {services[name]["image"] for name in ("app", "worker", "migrate")} == {IMAGE}
    assert services["worker"]["command"] == ["python", "-m", "mintflow.commands.receipt_worker"]
    assert services["migrate"]["command"] == ["alembic", "upgrade", "head"]
    assert services["migrate"]["profiles"] == ["tools"]
    assert services["worker"]["healthcheck"]["disable"] is True


def test_the_image_is_published_only_from_main_with_the_repository_token() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    job = workflow[workflow.index("\n  image:") :]

    assert "needs: quality" in job
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in job
    assert "permissions:\n      contents: read\n      packages: write" in job
    assert "password: ${{ secrets.GITHUB_TOKEN }}" in job
    assert "tags: ghcr.io/vladyslav-fdnk/mintflow:${{ github.sha }}" in job
    # Every other job keeps the workflow's read-only token.
    assert workflow.startswith("name: CI") and "\npermissions:\n  contents: read\n" in workflow


def test_the_image_runs_unprivileged_with_a_health_check() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "\nUSER mintflow\n" in dockerfile
    assert "HEALTHCHECK" in dockerfile and "/health/ready" in dockerfile
    assert '"--no-proxy-headers"' in dockerfile


def test_the_backup_tools_never_receive_the_app_secrets_or_a_private_key(
    services: dict[str, Any],
) -> None:
    backup = services["backup"]

    assert backup["profiles"] == ["tools"]
    assert backup["build"]["context"].endswith("deploy/tools")
    # Resolved without a backup.env, the service sees only the database login: the app's .env
    # (Telegram, email, signing keys) is never passed to it.
    assert set(backup["environment"]) == {"POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"}
    raw = (ROOT / "deploy" / "compose.production.yaml").read_text()
    section = raw[raw.index("\n  backup:") : raw.index("\n  postgres:")]
    assert "path: ${MINTFLOW_BACKUP_ENV_FILE:-backup.env}" in section
    assert "<<: *app" not in section
    assert [volume["target"] for volume in backup.get("volumes", [])] == ["/work"]


def test_the_tools_image_runs_unprivileged() -> None:
    dockerfile = (ROOT / "deploy" / "tools" / "Dockerfile").read_text()

    assert "postgresql17-client" in dockerfile and " age " in dockerfile
    assert "\nUSER backup\n" in dockerfile


def test_backups_expire_after_thirty_days() -> None:
    rules = json.loads((ROOT / "deploy" / "backup-lifecycle.json").read_text())["Rules"]

    assert rules == [
        {
            "ID": "expire-nightly-backups-after-30-days",
            "Status": "Enabled",
            "Filter": {"Prefix": "nightly/"},
            "Expiration": {"Days": 30},
        }
    ]
    example = (ROOT / "deploy" / "backup.env.example").read_text()
    assert "BACKUP_S3_PREFIX=nightly/" in example
    assert "AGE-SECRET-KEY" not in example
