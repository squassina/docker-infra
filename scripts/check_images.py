#!/usr/bin/env python3
"""Confere se cada imagem dos composes existe no registry e tem build para a arquitetura-alvo.

  Servidor 3 (Orange Pi 5 Plus)  -> linux/arm64
  stack-dns  (NanoPi NEO armv7)  -> linux/arm/v7

Requer o Docker com buildx (já presente nos runners do GitHub Actions).
  python3 scripts/check_images.py            # confere tudo
  python3 scripts/check_images.py --dry-run  # só lista imagem -> plataforma exigida
  python3 scripts/check_images.py --strict   # trata imagem não verificada (rede/limite) como falha
"""
import json
import os
import subprocess
import sys
import time
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


TRANSIENT_MARKERS = (
    "toomanyrequests", "too many requests", "rate limit", "429",
    "timeout", "timed out", "temporary failure", "connection reset", "connection refused",
    "unexpected eof", "502", "503", "504", "service unavailable", "tls handshake",
)


def is_transient(message: str) -> bool:
    """Erros de rede/limite de requisições: não provam que a imagem não existe."""
    low = message.lower()
    return any(m in low for m in TRANSIENT_MARKERS)


def inspect(image: str, attempts: int = 3, sleep=time.sleep) -> tuple[set[str] | None, str]:
    """(plataformas, erro). Repete com espera crescente quando o erro parece transitório."""
    err = ""
    for i in range(attempts):
        try:
            r = subprocess.run(
                ["docker", "buildx", "imagetools", "inspect", "--raw", image],
                capture_output=True, text=True, timeout=120,
            )
        except subprocess.TimeoutExpired:
            err = "timeout ao consultar o registry"
        else:
            if r.returncode == 0:
                try:
                    doc = json.loads(r.stdout)
                except json.JSONDecodeError:
                    return None, "resposta inválida do registry"
                plats = platforms_from_manifest(doc)
                if not plats:
                    return None, "imagem sem manifest multi-arquitetura (não dá para confirmar a plataforma)"
                return plats, ""
            err = (r.stderr.strip().splitlines() or ["falha ao consultar o registry"])[-1]
        if not is_transient(err):
            break
        if i < attempts - 1:
            sleep(2 ** (i + 1))
    return None, err


def check(image: str, required: set[str]) -> tuple[list[str], list[str]]:
    """(erros, avisos). Erro = imagem/plataforma ausente. Aviso = não deu para consultar (rede/limite)."""
    plats, err = inspect(image)
    if plats is None:
        msg = f"{image}: {err}"
        return ([], [msg]) if is_transient(err) else ([msg], [])
    errors = [f"{image}: sem build para {r} (disponíveis: {', '.join(sorted(plats))})"
              for r in sorted(required) if not satisfies(plats, r)]
    return errors, []


def annotate(level: str, msg: str) -> None:
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::{level}::{msg}")


def write_summary(rows: list[tuple[str, str, str]]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write("### Imagens e arquiteturas\n\n| Imagem | Plataforma exigida | Resultado |\n|---|---|---|\n")
        for img, req, res in rows:
            f.write(f"| `{img}` | {req} | {res} |\n")


def main() -> int:
    images = collect_images()
    if "--dry-run" in sys.argv:
        for img, req in sorted(images.items()):
            print(f"{img:60s} {', '.join(sorted(req))}")
        return 0
    strict = "--strict" in sys.argv
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda kv: (kv[0], kv[1], *check(*kv)), images.items()))
    errors = [m for _, _, errs, _ in results for m in errs]
    warnings = [m for _, _, _, warns in results for m in warns]
    rows = []
    for img, req, errs, warns in sorted(results, key=lambda r: r[0]):
        status = "FALHOU " if errs else ("AVISO  " if warns else "ok     ")
        print(status + img)
        rows.append((img, ", ".join(sorted(req)), "❌ " + errs[0].split(": ", 1)[1] if errs else
                     ("⚠️ não verificada: " + warns[0].split(": ", 1)[1] if warns else "✅")))
    write_summary(rows)
    for m in errors:
        annotate("error", m)
    for m in warnings:
        annotate("warning", m)
    if errors:
        print("\nProblemas:\n" + "\n".join(f"  - {m}" for m in errors))
    if warnings:
        print("\nNão verificadas (rede/limite de requisições; rode de novo):\n" + "\n".join(f"  - {m}" for m in warnings))
    if errors or (strict and warnings):
        return 1
    print(f"\n{len(images)} imagens: {len(images) - len(warnings)} verificadas, {len(warnings)} sem resposta.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
