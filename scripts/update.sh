#!/usr/bin/env bash
# Aplica no servidor as versões do repositório (ex.: depois de mesclar um PR do Dependabot).
#
#   scripts/update.sh              # Servidor 3: management -> media -> monitoring
#   scripts/update.sh --dns        # Servidores 1 e 2: stack-dns
#   scripts/update.sh stack-media  # só uma stack
#
# Faz: git pull -> docker compose pull -> up -d --remove-orphans -> limpa imagens antigas.
# --remove-orphans também remove containers de serviços que saíram do compose (ex.: o antigo watchtower).
# Para voltar atrás: git revert <commit> && scripts/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "${1:-}" = "--dns" ]; then shift; stacks=(stack-dns)
elif [ $# -gt 0 ]; then stacks=("$@")
else stacks=(stack-management stack-media stack-monitoring); fi

for s in "${stacks[@]}"; do
  [ -f "$s/docker-compose.yml" ] || { echo "stack desconhecida: $s"; exit 2; }
  [ -e "$s/.env" ] || { echo "$s/.env não existe (rode scripts/setup.sh ou crie o .env)"; exit 2; }
done

echo ">> git pull"
git pull --ff-only

for s in "${stacks[@]}"; do
  echo ">> $s: pull + up"
  ( cd "$s" && docker compose pull && docker compose up -d --remove-orphans )
done

docker image prune -f >/dev/null
echo "Pronto. Confira:  docker ps  e  docker compose -f <stack>/docker-compose.yml logs --tail 50"
