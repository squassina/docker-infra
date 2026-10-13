#!/usr/bin/env bash
# Valida os docker-compose.yml SEM precisar de segredos reais nem subir containers.
#   1. preenche os valores vazios do .env.example com "ci-dummy"
#   2. `docker compose config` (todos os profiles) -> sintaxe + interpolação
#   3. falha se o Compose avisar "variable is not set"
#   4. teste negativo: removendo uma variável obrigatória o Compose TEM de falhar (guarda ${VAR:?})
#
# Uso:  scripts/validate_compose.sh [stack ...]      (padrão: todas)
set -euo pipefail
cd "$(dirname "$0")/.."

if docker compose version >/dev/null 2>&1; then DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then DC=(docker-compose)
else echo "docker compose não encontrado"; exit 2; fi

declare -A ENVF=(
  [stack-management]=.env.example [stack-media]=.env.example [stack-monitoring]=.env.example
  [stack-dns]=stack-dns/.env.example )
declare -A REQ=(   # variável cuja ausência deve derrubar o Compose
  [stack-management]=CLOUDFLARE_DNS_API_TOKEN [stack-media]=SONARR_API_KEY
  [stack-monitoring]=GRAFANA_ADMIN_PASSWORD [stack-dns]=PIHOLE_ADMIN_PASSWORD )

stacks=("$@"); [ ${#stacks[@]} -gt 0 ] || stacks=(stack-management stack-media stack-monitoring stack-dns)
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
rc=0

for s in "${stacks[@]}"; do
  [ -n "${ENVF[$s]:-}" ] || { echo "stack desconhecida: $s"; exit 2; }
  sed -E 's/^([A-Z0-9_]+)=[[:space:]]*(#.*)?$/\1=ci-dummy/' "${ENVF[$s]}" > "$tmp/ok.env"

  if ! ( cd "$s" && "${DC[@]}" --env-file "$tmp/ok.env" --profile '*' config >"$tmp/out.yml" 2>"$tmp/err.txt" ); then
    echo "FALHOU   $s: docker compose config"; cat "$tmp/err.txt"; rc=1; continue
  fi
  if grep -qi 'is not set' "$tmp/err.txt"; then
    echo "FALHOU   $s: variável não definida no .env.example"; sort -u "$tmp/err.txt" | head -5; rc=1; continue
  fi

  grep -v "^${REQ[$s]}=" "$tmp/ok.env" > "$tmp/missing.env"
  if ( cd "$s" && "${DC[@]}" --env-file "$tmp/missing.env" --profile '*' config -q >/dev/null 2>&1 ); then
    echo "FALHOU   $s: ${REQ[$s]} é obrigatória, mas o Compose não reclamou da ausência (guarda \${VAR:?} removida?)"
    rc=1; continue
  fi
  echo "ok       $s"
done
exit $rc
