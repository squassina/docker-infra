"""Testes offline da lógica de scripts/check_images.py (não consultam registry)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_images", ROOT / "scripts/check_images.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def test_platforms_ignore_attestation_entries():
    doc = {"manifests": [
        {"platform": {"os": "linux", "architecture": "arm64"}},
        {"platform": {"os": "linux", "architecture": "arm", "variant": "v7"}},
        {"platform": {"os": "unknown", "architecture": "unknown"}},   # attestation do buildkit
    ]}
    assert ci.platforms_from_manifest(doc) == {"linux/arm64", "linux/arm/v7"}


def test_single_manifest_has_no_platforms():
    assert ci.platforms_from_manifest({"config": {}, "layers": []}) == set()


def test_satisfies_accepts_arm64_variant():
    assert ci.satisfies({"linux/arm64/v8"}, "linux/arm64")
    assert ci.satisfies({"linux/arm/v7"}, "linux/arm/v7")
    assert not ci.satisfies({"linux/arm64"}, "linux/arm/v7")
    assert not ci.satisfies({"linux/amd64"}, "linux/arm64")


def test_collect_images_assigns_the_right_platform_per_stack():
    images = ci.collect_images()
    traefik = next(i for i in images if i.startswith("traefik:v3."))
    assert images[traefik] == {"linux/arm64"}
    pihole = next(i for i in images if i.startswith("mpgirro/pihole-unbound"))
    assert images[pihole] == {"linux/arm/v7"}
    # prom/node-exporter aparece nos Pis (armv7) e no Servidor 3 (arm64)
    node = next(i for i in images if i.startswith("prom/node-exporter:"))
    assert images[node] == {"linux/arm/v7", "linux/arm64"}


def test_check_reports_missing_platform_as_error(monkeypatch):
    monkeypatch.setattr(ci, "inspect", lambda image: ({"linux/amd64"}, ""))
    errors, warnings = ci.check("x/y:1", {"linux/arm64"})
    assert len(errors) == 1 and "linux/arm64" in errors[0] and not warnings


def test_check_reports_missing_tag_as_error(monkeypatch):
    monkeypatch.setattr(ci, "inspect", lambda image: (None, "manifest unknown"))
    assert ci.check("x/y:1", {"linux/arm64"}) == (["x/y:1: manifest unknown"], [])


def test_rate_limit_is_a_warning_not_a_failure(monkeypatch):
    """Runners compartilhados batem no limite do Docker Hub: isso não prova que a tag não existe."""
    monkeypatch.setattr(ci, "inspect", lambda image: (None, "toomanyrequests: You have reached your pull rate limit"))
    errors, warnings = ci.check("x/y:1", {"linux/arm64"})
    assert errors == [] and len(warnings) == 1


def test_is_transient_classification():
    for msg in ("toomanyrequests: rate limit", "429 Too Many Requests", "i/o timeout", "503 Service Unavailable"):
        assert ci.is_transient(msg), msg
    for msg in ("manifest unknown", "not found", "denied: requested access to the resource is denied"):
        assert not ci.is_transient(msg), msg


class _Proc:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_inspect_retries_transient_errors_then_succeeds(monkeypatch):
    calls, naps = [], []
    seq = [_Proc(1, err="toomanyrequests"), _Proc(1, err="i/o timeout"),
           _Proc(0, out='{"manifests":[{"platform":{"os":"linux","architecture":"arm64"}}]}')]
    monkeypatch.setattr(ci.subprocess, "run", lambda *a, **k: calls.append(1) or seq[len(calls) - 1])
    plats, err = ci.inspect("x/y:1", sleep=naps.append)
    assert plats == {"linux/arm64"} and err == "" and len(calls) == 3 and naps == [2, 4]


def test_inspect_does_not_retry_definitive_errors(monkeypatch):
    calls = []
    monkeypatch.setattr(ci.subprocess, "run", lambda *a, **k: calls.append(1) or _Proc(1, err="manifest unknown"))
    plats, err = ci.inspect("x/y:1", sleep=lambda s: None)
    assert plats is None and err == "manifest unknown" and len(calls) == 1
