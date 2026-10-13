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


def test_check_reports_missing_platform(monkeypatch):
    monkeypatch.setattr(ci, "inspect", lambda image: ({"linux/amd64"}, ""))
    msgs = ci.check("x/y:1", {"linux/arm64"})
    assert len(msgs) == 1 and "linux/arm64" in msgs[0]


def test_check_reports_registry_error(monkeypatch):
    monkeypatch.setattr(ci, "inspect", lambda image: (None, "manifest unknown"))
    assert ci.check("x/y:1", {"linux/arm64"}) == ["x/y:1: manifest unknown"]
