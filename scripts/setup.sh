#!/usr/bin/env bash
# Prepara o Servidor 3: symlinks do .env, diretórios, permissões e a rede proxy-net.
# Uso:  cp .env.example .env  (preencha)  &&  ./scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || { echo "Crie o .env primeiro:  cp .env.example .env  (e preencha)"; exit 1; }
chmod 600 .env

# Lê uma variável do .env SEM fazer 'source' (senhas com caracteres especiais quebrariam o shell).
get() {
  grep -E "^$1=" .env | tail -n1 | cut -d= -f2- \
    | sed -E "s/[[:space:]]+#.*$//; s/^['\"]//; s/['\"]$//"
}

CONFIG_DIR="$(get CONFIG_DIR)"; DATA_DIR="$(get DATA_DIR)"
PUID="$(get PUID)"; PGID="$(get PGID)"
[ -n "$CONFIG_DIR" ] && [ -n "$DATA_DIR" ] || { echo "CONFIG_DIR e DATA_DIR precisam estar definidos no .env"; exit 1; }

for s in stack-management stack-media stack-monitoring; do
  ln -sfn ../.env "$s/.env"
done
echo "Symlinks do .env criados."

sudo mkdir -p "$CONFIG_DIR"/{traefik/letsencrypt,portainer,homepage,prometheus/data,grafana,seerr}
sudo chown -R 65534:65534 "$CONFIG_DIR/prometheus"   # Prometheus roda como "nobody"
sudo chown -R 472:472     "$CONFIG_DIR/grafana"      # Grafana roda como UID 472
sudo chown -R 1000:1000   "$CONFIG_DIR/seerr"        # Seerr sempre roda como UID 1000

# torrents/ e media/ DEVEM ficar no mesmo filesystem (hardlinks)
sudo mkdir -p "$DATA_DIR"/torrents/{movies,tv,music} "$DATA_DIR"/media/{movies,tv,music}
sudo chown -R "${PUID:-1000}:${PGID:-1000}" "$DATA_DIR"

docker network inspect proxy-net >/dev/null 2>&1 || docker network create proxy-net
echo "Pronto. Suba as stacks nesta ordem: management -> media -> monitoring."
