#!/usr/bin/env python3
"""Confere se cada imagem dos composes existe no registry e tem build para a arquitetura-alvo.

  Servidor 3 (Orange Pi 5 Plus)  -> linux/arm64
  stack-dns  (NanoPi NEO armv7)  -> linux/arm/v7

Requer o Docker com buildx (já presente nos runners do GitHub Actions).
  python3 scripts/check_images.py            # confere tudo
  python3 scripts/check_images.py --dry-run  # só lista imagem -> plataforma exigida
"""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TARGETS = {
    "stack-management": "linux/arm64",
    "stack-media": "linux/arm64",
    "stack-monitoring": "linux/arm64",
    "stack-dns": "linux/arm/v7",
}


def collect_images(root: Path = ROOT) -> dict[str, set[str]]:
    """{imagem: {plataformas exigidas}} lendo os docker-compose.yml."""
    found: dict[str, set[str]] = {}
    for stack, platform in TARGETS.items():
        data = yaml.safe_load((root / stack / "docker-compose.yml").read_text(encoding="utf-8"))
        for svc in data["services"].values():
            found.setdefault(svc["image"], set()).add(platform)
    return found


def platforms_from_manifest(doc: dict) -> set[str]:
    """Plataformas (os/arch[/variant]) de um manifest list / OCI index. Ignora attestations."""
    out = set()
    for m in doc.get("manifests", []):
        p = m.get("platform") or {}
        if not p.get("os") or p.get("os") == "unknown":
            continue
        out.add("/".join(x for x in (p["os"], p.get("architecture"), p.get("variant")) if x))
    return out


def satisfies(available: set[str], required: str) -> bool:
    if required in available:
        return True
    # arm64 pode vir com variante (linux/arm64/v8)
    return required == "linux/arm64" and "linux/arm64/v8" in available


def inspect(image: str) -> tuple[set[str] | None, str]:
    r = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", "--raw", image],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        return None, (r.stderr.strip().splitlines() or ["falha ao consultar o registry"])[-1]
    try:
        doc = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None, "resposta inválida do registry"
    plats = platforms_from_manifest(doc)
    if not plats:
        return None, "imagem sem manifest multi-arquitetura (não dá para confirmar a plataforma)"
    return plats, ""


def check(image: str, required: set[str]) -> list[str]:
    plats, err = inspect(image)
    if plats is None:
        return [f"{image}: {err}"]
    return [f"{image}: sem build para {r} (disponíveis: {', '.join(sorted(plats))})"
            for r in sorted(required) if not satisfies(plats, r)]


def main() -> int:
    images = collect_images()
    if "--dry-run" in sys.argv:
        for img, req in sorted(images.items()):
            print(f"{img:60s} {', '.join(sorted(req))}")
        return 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda kv: (kv[0], check(*kv)), images.items()))
    bad = [msg for _, msgs in results for msg in msgs]
    for img, msgs in sorted(results):
        print(("FALHOU  " if msgs else "ok      ") + img)
    if bad:
        print("\nProblemas:")
        print("\n".join(f"  - {b}" for b in bad))
        return 1
    print(f"\n{len(images)} imagens verificadas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
