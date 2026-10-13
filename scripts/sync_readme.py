#!/usr/bin/env python3
"""Mantém os blocos de código do README idênticos aos arquivos do repositório.

Cada bloco do README precedido por  <!-- file: caminho/do/arquivo -->  é substituído
pelo conteúdo desse arquivo. Os ARQUIVOS são a fonte da verdade.

  python3 scripts/sync_readme.py          # atualiza o README
  python3 scripts/sync_readme.py --check  # só verifica (usado no CI); sai com 1 se houver diferença
"""
import difflib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

# marcador, linha de abertura da cerca, bloco inteiro
PATTERN = re.compile(
    r"(?P<marker><!-- file: (?P<path>[^ >]+) -->\n)"
    r"(?P<open>```[\w-]*\n)"
    r"(?P<body>.*?)"
    r"(?P<close>^```$)",
    re.S | re.M,
)


def render(text: str) -> tuple[str, list[str]]:
    problems: list[str] = []

    def repl(m: re.Match) -> str:
        path = ROOT / m.group("path")
        if not path.is_file():
            problems.append(f"arquivo referenciado no README não existe: {m.group('path')}")
            return m.group(0)
        body = path.read_text(encoding="utf-8").rstrip("\n") + "\n"
        return f"{m.group('marker')}{m.group('open')}{body}{m.group('close')}"

    return PATTERN.sub(repl, text), problems


def main() -> int:
    check = "--check" in sys.argv[1:]
    current = README.read_text(encoding="utf-8")
    updated, problems = render(current)

    if not PATTERN.search(current):
        problems.append("nenhum marcador '<!-- file: ... -->' encontrado no README")
    for p in problems:
        print(f"ERRO: {p}")
    if problems:
        return 1

    if updated == current:
        print("README em sincronia com os arquivos.")
        return 0

    if check:
        diff = difflib.unified_diff(
            current.splitlines(), updated.splitlines(), "README.md (atual)", "README.md (esperado)", lineterm="", n=1
        )
        print("\n".join(list(diff)[:80]))
        print("\nREADME fora de sincronia. Rode:  python3 scripts/sync_readme.py")
        return 1

    README.write_text(updated, encoding="utf-8")
    print("README atualizado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
