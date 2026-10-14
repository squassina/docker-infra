"""Convenções dos workflows do GitHub Actions."""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))

# Majors que rodam em Node 20. Node 20 foi removido dos runners do GitHub em 16/09/2026:
# estas versões FALHAM na primeira etapa.
NODE20_ACTIONS = {
    "actions/checkout": {"v1", "v2", "v3", "v4"},
    "actions/setup-python": {"v1", "v2", "v3", "v4", "v5"},
    "actions/cache": {"v1", "v2", "v3"},
    "actions/upload-artifact": {"v1", "v2", "v3"},
    "gitleaks/gitleaks-action": {"v1", "v2"},
}


def load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if True in data:             # PyYAML lê a chave `on:` como booleano True
        data["on"] = data.pop(True)
    return data


def steps(wf: dict):
    for job_name, job in wf["jobs"].items():
        for step in job.get("steps", []):
            yield job_name, step


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_action_uses_the_removed_node20_runtime(path):
    for job, step in steps(load(path)):
        uses = step.get("uses", "")
        name, _, ref = uses.partition("@")
        assert ref not in NODE20_ACTIONS.get(name, set()), f"{path.name}/{job}: {uses} roda em Node 20 (removido em 16/09/2026)"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_job_has_a_timeout_and_least_privilege(path):
    wf = load(path)
    assert wf["permissions"] == {"contents": "read"}, "permissões do workflow: só leitura"
    for name, job in wf["jobs"].items():
        assert "timeout-minutes" in job, f"{path.name}/{name}: sem timeout-minutes"


def test_validate_runs_on_the_default_branch_and_pull_requests():
    on = load(ROOT / ".github/workflows/validate.yml")["on"]
    assert {"main", "master"} <= set(on["push"]["branches"]), "o repositório usa master (e pode migrar para main)"
    assert "pull_request" in on and "workflow_dispatch" in on


def test_validate_has_a_single_gate_job_covering_all_others():
    wf = load(ROOT / ".github/workflows/validate.yml")
    others = set(wf["jobs"]) - {"ci-ok"}
    assert set(wf["jobs"]["ci-ok"]["needs"]) == others, "o CI ok precisa depender de todos os jobs"
    assert wf["jobs"]["ci-ok"]["if"] == "always()"


def test_gitleaks_job_can_read_pull_requests():
    perms = load(ROOT / ".github/workflows/validate.yml")["jobs"]["secrets"]["permissions"]
    assert perms == {"contents": "read", "pull-requests": "read"}


def test_images_workflow_schedule_and_triggers():
    on = load(ROOT / ".github/workflows/images.yml")["on"]
    cron = on["schedule"][0]["cron"]
    assert len(cron.split()) == 5
    assert "workflow_dispatch" in on
    paths = on["pull_request"]["paths"]
    assert "stack-*/docker-compose.yml" in paths and "scripts/check_images.py" in paths


def test_images_workflow_docker_hub_login_is_optional():
    text = (ROOT / ".github/workflows/images.yml").read_text(encoding="utf-8")
    assert "env.DOCKERHUB_USERNAME != ''" in text, "sem os segredos o job deve seguir anônimo, não falhar"


def test_checkout_depth_for_gitleaks_scans_full_history():
    job = load(ROOT / ".github/workflows/validate.yml")["jobs"]["secrets"]
    checkout = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout"))
    assert checkout["with"]["fetch-depth"] == 0


def test_runners_are_pinned_not_latest():
    """ubuntu-latest migra para o Ubuntu 26 em 19/10/2026: mude de versão de propósito, não por surpresa."""
    for path in WORKFLOWS:
        for name, job in load(path)["jobs"].items():
            assert job["runs-on"] != "ubuntu-latest", f"{path.name}/{name}: fixe a versão do runner (ex.: ubuntu-24.04)"


def test_lint_requirements_are_pinned_and_used_by_the_lint_job():
    lines = [l.strip() for l in (ROOT / "tests/requirements-lint.txt").read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")]
    assert any(l.startswith("shellcheck-py==") for l in lines) and any(l.startswith("yamllint==") for l in lines)
    assert all("==" in l for l in lines), "linters devem ter versão exata (o shellcheck do runner diferia do local)"
    runs = " ".join(s.get("run", "") for s in load(ROOT / ".github/workflows/validate.yml")["jobs"]["lint"]["steps"])
    assert "tests/requirements-lint.txt" in runs and "shellcheck" in runs


def test_no_python_bytecode_is_tracked_and_gitignore_covers_it():
    import shutil
    import subprocess
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    assert "__pycache__/" in ignore and "*.pyc" in ignore
    if shutil.which("git") and (ROOT / ".git").exists():
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
        bad = [f for f in tracked if "__pycache__" in f or f.endswith(".pyc")]
        assert not bad, f"bytecode versionado: {bad}"
