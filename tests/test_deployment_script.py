"""Exercise deployment failure gates without touching Docker or a real database."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

RELEASE = "a" * 40
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def deployment(tmp_path):
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    for name in ("deploy.sh", "backup.sh"):
        shutil.copy2(SCRIPTS / name, root / "scripts" / name)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    docker = binaries / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        'args = " ".join(sys.argv[1:])\n'
        'with open(os.environ["DOCKER_TRACE"], "a") as f: f.write(args + "\\n")\n'
        'if os.environ.get("FAIL_MATCH") and os.environ["FAIL_MATCH"] in args: sys.exit(9)\n'
        'if "pg_dump" in args: sys.stdout.write("fake-backup")\n'
    )
    docker.chmod(0o755)
    flock = binaries / "flock"
    flock.write_text("#!/bin/sh\nexit 0\n")
    flock.chmod(0o755)
    env = dict(
        os.environ, PATH=f"{binaries}:{os.environ['PATH']}", DOCKER_TRACE=str(root / "trace")
    )
    return root, env


def run_deploy(deployment, fail=""):
    root, env = deployment
    env["FAIL_MATCH"] = fail
    result = subprocess.run(
        ["bash", str(root / "scripts/deploy.sh"), RELEASE], env=env, capture_output=True, text=True
    )
    return root, result, (root / "trace").read_text()


def test_success_records_release_only_after_migration_and_health(deployment):
    root, result, trace = run_deploy(deployment)
    assert result.returncode == 0, result.stderr
    assert (root / ".deployed-release").read_text().strip() == RELEASE
    assert (
        trace.index("pg_dump")
        < trace.index("stop nginx app")
        < trace.index("run --rm --no-deps migrate")
    )
    assert trace.index("run --rm --no-deps migrate") < trace.index("--force-recreate --wait")
    assert len(list((root / "backups").glob("*.dump"))) == 1


@pytest.mark.parametrize(
    "failure",
    ["check_production.py", "pg_dump", "run --rm --no-deps migrate", "--force-recreate --wait"],
)
def test_failure_never_records_a_successful_release(deployment, failure):
    root, result, trace = run_deploy(deployment, failure)
    assert result.returncode != 0
    assert not (root / ".deployed-release").exists()
    if failure in ("check_production.py", "pg_dump"):
        assert "stop nginx app" not in trace
        assert "run --rm --no-deps migrate" not in trace
