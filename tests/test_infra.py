"""Testes de convenções do repositório (rodam sem Docker).

Complementam `docker compose config`, que só valida sintaxe/interpolação.
"""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent

SERVER3_STACKS = ["stack-management", "stack-media", "stack-monitoring"]
ALL_STACKS = SERVER3_STACKS + ["stack-dns"]

# Variáveis documentadas no .env.example mas não usadas pelos composes do Servidor 3.
DOCUMENTARY_VARS = {"SERVER3_IP"}  # usada só pelo stack-dns (cada Pi tem o seu .env)

# Serviços que NUNCA devem ser atualizados automaticamente pelo Watchtower.
CRITICAL = {"traefik", "portainer", "watchtower", "prometheus", "grafana", "pihole-unbound", "tailscale"}


# ----------------------------------------------------------------- helpers
class _Loader(yaml.SafeLoader):
    pass


_Loader.add_constructor("!env_var", lambda loader, node: loader.construct_scalar(node))


def load_yaml(path: Path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)


def compose(stack: str) -> dict:
    return load_yaml(ROOT / stack / "docker-compose.yml")


def labels_of(service: dict) -> dict:
    raw = service.get("labels") or {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    out = {}
    for item in raw:
        k, _, v = str(item).partition("=")
        out[k] = v
    return out


def parse_env(path: Path) -> dict:
    """Parser mínimo de .env: ignora comentários, aceita aspas e comentário inline."""
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if val.startswith(("'", '"')):
            q = val[0]
            end = val.find(q, 1)
            val = val[1:end] if end != -1 else val[1:]
        elif val.startswith("#"):
            val = ""
        else:
            val = re.split(r"\s+#", val, maxsplit=1)[0].strip()
        values[key.strip()] = val
    return values


def env_example_for(stack: str) -> Path:
    return ROOT / ("stack-dns" if stack == "stack-dns" else "") / ".env.example"


VAR_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:?[-?+][^}]*)?\}")


def referenced_vars(stack: str):
    """{(nome, tem_default)} de cada ${VAR} usada no compose. ${VAR:-x} tem default; ${VAR:?msg} não."""
    text = (ROOT / stack / "docker-compose.yml").read_text(encoding="utf-8")
    out = set()
    for m in VAR_REF.finditer(text):
        modifier = m.group(2) or ""
        out.add((m.group(1), modifier.lstrip(":").startswith("-")))
    return out


def traefik_services():
    """(stack, nome_do_serviço, definição, labels) de todo serviço com traefik.enable=true."""
    for stack in SERVER3_STACKS:
        for name, svc in compose(stack)["services"].items():
            lab = labels_of(svc)
            if lab.get("traefik.enable") == "true":
                yield stack, name, svc, lab


def networks_of(svc: dict) -> set:
    n = svc.get("networks") or []
    return set(n if isinstance(n, list) else n.keys())


# ----------------------------------------------------------------- compose
@pytest.mark.parametrize("stack", ALL_STACKS)
def test_compose_has_no_obsolete_version_key(stack):
    assert "version" not in compose(stack), "a chave 'version:' é obsoleta no Compose v2"


@pytest.mark.parametrize("stack", ALL_STACKS)
def test_every_service_has_restart_policy(stack):
    for name, svc in compose(stack)["services"].items():
        assert svc.get("restart") == "unless-stopped", f"{stack}/{name}: faltou restart: unless-stopped"


def test_container_names_are_unique_on_server3():
    names = []
    for stack in SERVER3_STACKS:
        names += [s.get("container_name", n) for n, s in compose(stack)["services"].items()]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"container_name duplicado entre stacks: {dupes}"


def test_proxy_net_is_always_external():
    for stack in SERVER3_STACKS:
        nets = compose(stack).get("networks", {})
        if "proxy-net" in nets:
            assert nets["proxy-net"].get("external") is True, (
                f"{stack}: proxy-net deve ser external (criada com 'docker network create proxy-net')"
            )


# ----------------------------------------------------------------- traefik
def test_traefik_labels_are_complete():
    seen = 0
    for stack, name, svc, lab in traefik_services():
        seen += 1
        routers = {k.split(".")[3] for k in lab if k.startswith("traefik.http.routers.")}
        assert routers, f"{stack}/{name}: sem router"
        for r in routers:
            rule = lab.get(f"traefik.http.routers.{r}.rule", "")
            assert rule.startswith("Host(`") and "${MY_DOMAIN" in rule, f"{stack}/{name}: rule inválida em {r}: {rule!r}"
            if lab.get(f"traefik.http.routers.{r}.service") != "api@internal":
                assert lab.get(f"traefik.http.routers.{r}.entrypoints") == "websecure", f"{stack}/{name}: entrypoint != websecure"
                assert f"traefik.http.services.{r}.loadbalancer.server.port" in lab, f"{stack}/{name}: faltou a porta do serviço"
    assert seen >= 10, "esperava ao menos 10 serviços publicados pelo Traefik"


def test_traefik_services_are_on_proxy_net_and_pin_the_network():
    for stack, name, svc, lab in traefik_services():
        nets = networks_of(svc)
        assert "proxy-net" in nets, f"{stack}/{name}: tem labels do Traefik mas não está na proxy-net"
        has_backend = any(k.endswith("loadbalancer.server.port") for k in lab)   # api@internal não tem backend
        if len(nets) > 1 and has_backend:
            assert lab.get("traefik.docker.network") == "proxy-net", (
                f"{stack}/{name}: está em várias redes; defina traefik.docker.network=proxy-net"
            )


def test_router_names_and_hostnames_are_unique():
    routers, hosts = [], []
    for stack, name, svc, lab in traefik_services():
        for k, v in lab.items():
            m = re.match(r"traefik\.http\.routers\.([^.]+)\.rule$", k)
            if m:
                routers.append(m.group(1))
                hosts.append(v)
    assert len(routers) == len(set(routers)), f"router duplicado: {sorted(r for r in routers if routers.count(r) > 1)}"
    assert len(hosts) == len(set(hosts)), "dois serviços respondem pelo mesmo Host()"


def test_traefik_command_uses_wildcard_cert_and_public_resolvers():
    cmd = " ".join(compose("stack-management")["services"]["traefik"]["command"])
    assert "tls.domains[0].sans=*." in cmd, "certificado wildcard ausente"
    assert "dnschallenge.resolvers=" in cmd, "sem resolvers públicos para o DNS-01 (o Pi-hole sobrescreve o domínio)"
    assert "exposedbydefault=false" in cmd
    assert "providers.docker.network=proxy-net" in cmd


def test_traefik_image_is_pinned_to_v3_6_or_newer():
    image = compose("stack-management")["services"]["traefik"]["image"]
    m = re.fullmatch(r"traefik:v?(\d+)\.(\d+)(?:\.\d+)?", image)
    assert m, f"fixe a versão do Traefik (achei {image!r})"
    assert (int(m.group(1)), int(m.group(2))) >= (3, 6), "Traefik < 3.6.1 não funciona com Docker Engine 29+"


def test_prometheus_scrapes_the_traefik_metrics_port():
    cmd = " ".join(compose("stack-management")["services"]["traefik"]["command"])
    assert "--entrypoints.metrics.address=:8082" in cmd
    prom = load_yaml(ROOT / "stack-monitoring/prometheus/prometheus.yml")
    targets = [t for job in prom["scrape_configs"] for sc in job["static_configs"] for t in sc["targets"]]
    assert "traefik:8082" in targets


# ----------------------------------------------------------------- versões fixas
FLOATING_TAGS = {"latest", "lts", "stable", "main", "master", "develop", "dev", "nightly", "edge", "testing", "preview"}


def all_images():
    for stack in ALL_STACKS:
        for name, svc in compose(stack)["services"].items():
            yield stack, name, svc["image"]


def test_every_image_is_pinned_to_an_explicit_version():
    """Sem :latest/:lts/:stable. O Dependabot propõe as atualizações como PR."""
    for stack, name, image in all_images():
        repo, _, tag = image.rpartition(":")
        assert repo and "/" not in tag, f"{stack}/{name}: imagem sem tag ({image})"
        assert tag not in FLOATING_TAGS, f"{stack}/{name}: tag flutuante ({image})"
        assert re.search(r"\d", tag), f"{stack}/{name}: a tag deveria conter uma versão ({image})"


def test_watchtower_was_removed():
    """Com tags fixas o Watchtower não faz nada (ele só re-baixa a mesma tag) e exigia docker.sock em escrita."""
    text = " ".join((ROOT / s / "docker-compose.yml").read_text(encoding="utf-8").lower() for s in ALL_STACKS)
    assert "watchtower" not in text


def test_recyclarr_is_pinned_to_a_v8_release():
    """O Recyclarr não publica mais a tag 'latest' (desde 8.4.0) e o trash_id em perfis só existe na v8+."""
    image = compose("stack-media")["services"]["recyclarr"]["image"]
    m = re.fullmatch(r"ghcr\.io/recyclarr/recyclarr:(\d+)\.\d+\.\d+", image)
    assert m and int(m.group(1)) >= 8, image


def test_images_use_the_registries_that_actually_publish_them():
    images = {name: image for _, name, image in all_images() if name in {"unpackerr", "seerr", "flaresolverr"}}
    assert images["unpackerr"].startswith("ghcr.io/unpackerr/unpackerr:"), "o Docker Hub do Unpackerr é opcional"
    assert images["seerr"].startswith("ghcr.io/seerr-team/seerr:")


# ----------------------------------------------------------------- mídia
def test_arr_apps_share_a_single_data_mount():
    """Hardlinks só funcionam se torrents/ e media/ estiverem sob o MESMO mount."""
    services = compose("stack-media")["services"]
    for name in ("qbittorrent", "sonarr", "radarr", "lidarr"):
        vols = " ".join(str(v) for v in services[name]["volumes"])
        assert re.search(r"\$\{DATA_DIR(:\?[^}]*)?\}:/data(\s|$)", vols), f"{name} deve montar ${{DATA_DIR}}:/data"


def test_no_legacy_split_mounts_in_media_stack():
    text = (ROOT / "stack-media/docker-compose.yml").read_text(encoding="utf-8")
    assert "DOWNLOADS_DIR" not in text and "MEDIA_DIR}" not in text, "layout antigo (/downloads + /tv) quebra hardlinks e o Bazarr"


def test_media_ui_ports_are_not_published_on_the_host():
    for name, svc in compose("stack-media")["services"].items():
        for p in svc.get("ports", []):
            assert str(p).startswith("6881:"), f"{name}: porta {p} exposta no host; use o Traefik"


def test_jellyfin_has_rockchip_devices():
    devs = " ".join(compose("stack-media")["services"]["jellyfin"]["devices"])
    for node in ("/dev/mpp_service", "/dev/rga", "/dev/dma_heap", "/dev/mali0", "/dev/dri"):
        assert node in devs, f"jellyfin sem {node} (RKMPP no RK3588)"


def test_jellyseerr_was_replaced_by_seerr():
    services = compose("stack-media")["services"]
    assert "seerr" in services and "jellyseerr" not in services
    assert services["seerr"]["image"].startswith("ghcr.io/seerr-team/seerr")


def test_recyclarr_env_vars_are_passed_to_the_container():
    cfg = (ROOT / "recyclarr/recyclarr.yml").read_text(encoding="utf-8")
    needed = set(re.findall(r"!env_var\s+(\w+)", cfg))
    assert needed, "recyclarr.yml deveria usar !env_var"
    env = compose("stack-media")["services"]["recyclarr"]["environment"]
    assert needed <= set(env), f"faltam no container do Recyclarr: {needed - set(env)}"


def test_recyclarr_uses_quality_profile_trash_ids_and_mapping_syntax():
    cfg = load_yaml(ROOT / "recyclarr/recyclarr.yml")
    for app in ("sonarr", "radarr"):
        for inst in cfg[app].values():
            for qp in inst["quality_profiles"]:
                assert "trash_id" in qp
                assert qp["reset_unmatched_scores"] == {"enabled": True}, "reset_unmatched_scores é um mapa (enabled: true)"


# ----------------------------------------------------------------- pi-hole v6
def test_pihole_uses_v6_variables_only():
    svc = compose("stack-dns")["services"]["pihole-unbound"]
    env = svc["environment"]
    legacy = {"WEBPASSWORD", "FTLCONF_LOCAL_IPV4", "REV_SERVER", "REV_SERVER_TARGET", "REV_SERVER_CIDR", "REV_SERVER_DOMAIN", "PIHOLE_DNS_"}
    assert not (legacy & set(env)), f"variáveis do Pi-hole v5 não funcionam mais: {legacy & set(env)}"
    assert "FTLCONF_webserver_api_password" in env
    assert env["FTLCONF_dns_listeningMode"] == "all", "em rede bridge o listeningMode deve ser 'all'"
    assert env["FTLCONF_dns_upstreams"] == "127.0.0.1#5335"
    assert "cbcrowe" not in svc["image"], "imagem cbcrowe/pihole-unbound está abandonada (Pi-hole v5)"
    assert not svc["image"].endswith(":latest"), "fixe a tag do Pi-hole"


def test_tailscale_advertises_routes_and_does_not_accept_dns():
    env = compose("stack-dns")["services"]["tailscale"]["environment"]
    assert env["TS_ACCEPT_DNS"] == "false", "nó de DNS não deve aceitar o DNS da tailnet (loop)"
    assert "TS_ROUTES" in env and "TS_HOSTNAME" in env


def test_dns_stack_does_not_receive_server3_secrets():
    env = parse_env(ROOT / "stack-dns/.env.example")
    forbidden = {"CLOUDFLARE_DNS_API_TOKEN", "SONARR_API_KEY", "RADARR_API_KEY", "GRAFANA_ADMIN_PASSWORD", "TRAEFIK_DASHBOARD_AUTH"}
    assert not (forbidden & set(env)), "os Pis de DNS não precisam desses segredos"


# ----------------------------------------------------------------- variáveis de ambiente
@pytest.mark.parametrize("stack", ALL_STACKS)
def test_every_variable_used_exists_in_env_example(stack):
    example = parse_env(env_example_for(stack))
    missing = sorted(n for n, has_default in referenced_vars(stack) if not has_default and n not in example)
    assert not missing, f"{stack} usa variáveis ausentes no .env.example: {missing}"


def test_no_unused_variables_in_server3_env_example():
    example = parse_env(ROOT / ".env.example")
    used = set()
    for stack in SERVER3_STACKS:
        used |= {n for n, _ in referenced_vars(stack)}
    # variáveis lidas por scripts/setup.sh também contam
    used |= set(re.findall(r'get (\w+)', (ROOT / "scripts/setup.sh").read_text(encoding="utf-8")))
    unused = sorted(set(example) - used - DOCUMENTARY_VARS)
    assert not unused, f"variáveis sem uso no .env.example: {unused}"


def test_dns_env_example_has_no_unused_variables():
    example = parse_env(ROOT / "stack-dns/.env.example")
    used = {n for n, _ in referenced_vars("stack-dns")}
    assert not (set(example) - used), f"variáveis sem uso: {sorted(set(example) - used)}"


@pytest.mark.parametrize("example", [".env.example", "stack-dns/.env.example"])
def test_env_examples_contain_only_placeholders(example):
    secretish = re.compile(r"(TOKEN|PASSWORD|API_KEY|AUTHKEY|AUTH)$")
    placeholder = re.compile(r"(seu|sua|troque|aqui|exemplo|^$)", re.I)
    for key, val in parse_env(ROOT / example).items():
        if secretish.search(key):
            assert placeholder.search(val), f"{example}: {key} parece conter um valor real"


def test_gitignore_blocks_env_files():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    assert ".env" in lines and "**/.env" in lines


# ----------------------------------------------------------------- socket proxy
SOCKET_USERS = {"traefik": "socket-proxy-traefik", "homepage": "socket-proxy-homepage"}


def _proxy_flags(name: str) -> dict:
    """{'allowGET': [regex...], 'allowfrom': 'traefik', ...} a partir do command do socket-proxy."""
    flags: dict = {}
    for item in compose("stack-management")["services"][name]["command"]:
        key, _, val = item.lstrip("-").partition("=")
        flags.setdefault(key, []).append(val)
    return flags


def _allowed(name: str, method: str, path: str) -> bool:
    """O socket-proxy coloca ^...$ em volta do padrão (regex do Go, compatível com re.fullmatch aqui)."""
    patterns = _proxy_flags(name).get(f"allow{method}", [])
    return any(re.fullmatch(p, path) for p in patterns)


def test_docker_socket_is_mounted_only_by_the_proxies_and_portainer():
    holders = set()
    for stack in ALL_STACKS:
        for name, svc in compose(stack)["services"].items():
            if any("docker.sock" in str(v) for v in svc.get("volumes", [])):
                holders.add(name)
    # portainer-agent precisa do socket completo (gerencia os nós); portainer idem
    assert holders == {"socket-proxy-traefik", "socket-proxy-homepage", "portainer", "portainer-agent"}, holders


def test_traefik_reads_docker_through_its_socket_proxy():
    svc = compose("stack-management")["services"]["traefik"]
    cmd = " ".join(svc["command"])
    assert "--providers.docker.endpoint=tcp://socket-proxy-traefik:2375" in cmd
    assert not any("docker.sock" in str(v) for v in svc["volumes"])
    assert svc["depends_on"]["socket-proxy-traefik"]["condition"] == "service_healthy"


@pytest.mark.parametrize("client,proxy", SOCKET_USERS.items())
def test_socket_proxy_is_locked_down(client, proxy):
    cfg = compose("stack-management")
    svc, flags = cfg["services"][proxy], _proxy_flags(proxy)
    assert flags["allowfrom"] == [client], "só o cliente pode conectar"
    assert cfg["services"][client].get("container_name", client) == client, "allowfrom resolve pelo nome do container"
    assert not (set(flags) & {"allowPOST", "allowPUT", "allowDELETE", "allowPATCH", "allowCONNECT"}), "somente leitura"
    assert svc["read_only"] is True and svc["cap_drop"] == ["ALL"]
    assert "ports" not in svc and "expose" not in svc
    assert svc["user"].startswith("65534:") and "DOCKER_GID" in svc["user"]
    assert svc["image"].startswith("wollomatic/socket-proxy:1.")
    # rede própria e INTERNA; fora da proxy-net (que tem as UIs publicadas)
    net = next(iter(svc["networks"]))
    assert svc["networks"] == [net] and cfg["networks"][net]["internal"] is True
    assert "proxy-net" not in svc["networks"]
    assert net in cfg["services"][client]["networks"]


def test_traefik_socket_proxy_requires_the_head_ping_rule():
    """Traefik >= 3.6.1 faz HEAD /_ping; sem a regra ele cai numa versão de API errada e o provider quebra."""
    assert "/_ping" in _proxy_flags("socket-proxy-traefik")["allowHEAD"]


def test_traefik_proxy_allowlist_matches_what_traefik_needs_and_nothing_else():
    p = "socket-proxy-traefik"
    for ok in ("/v1.51/version", "/v1.51/containers/json", "/v1.51/containers/0123abcd/json", "/v1.51/events"):
        assert _allowed(p, "GET", ok), ok
    for bad in ("/v1.51/images/json", "/v1.51/info", "/v1.51/volumes", "/v1.51/networks", "/v1.51/secrets", "/version"):
        assert not _allowed(p, "GET", bad), bad
    assert not _allowed(p, "POST", "/v1.51/containers/create")


def test_homepage_proxy_allows_only_container_list_inspect_and_stats():
    p = "socket-proxy-homepage"
    for ok in ("/containers/json", "/v1.51/containers/json", "/containers/sonarr/json", "/containers/sonarr/stats"):
        assert _allowed(p, "GET", ok), ok
    for bad in ("/containers/sonarr/archive", "/containers/sonarr/logs", "/containers/sonarr/exec",
                "/images/json", "/info", "/volumes", "/containers/a/b/json"):
        assert not _allowed(p, "GET", bad), bad
    assert not _allowed(p, "POST", "/containers/sonarr/exec")


def test_docker_gid_is_required_and_listed():
    assert "DOCKER_GID" in parse_env(ROOT / ".env.example")
    text = (ROOT / "stack-management/docker-compose.yml").read_text(encoding="utf-8")
    assert "${DOCKER_GID:?" in text


# ----------------------------------------------------------------- nebula-sync (réplica dos Pi-holes)
def _nebula() -> dict:
    return compose("stack-management")["services"]["nebula-sync"]


def test_nebula_sync_is_selective_not_full_sync():
    """FULL_SYNC=true importaria o Teleporter inteiro e sobrescreveria a config que vem do Compose de cada nó."""
    env = _nebula()["environment"]
    assert str(env["FULL_SYNC"]).lower() == "false"
    for key in ("SYNC_GRAVITY_GROUP", "SYNC_GRAVITY_AD_LIST", "SYNC_GRAVITY_DOMAIN_LIST", "SYNC_GRAVITY_CLIENT"):
        assert str(env[key]).lower() == "true", key


def test_nebula_sync_never_syncs_config_owned_by_the_compose_files():
    env = _nebula()["environment"]
    owned_by_compose = {"upstreams", "dnssec", "revServers", "listeningMode", "interface", "dnsmasq_lines"}
    included = set(env["SYNC_CONFIG_DNS_INCLUDE"].split(","))
    assert included == {"hosts", "cnameRecords"}, "só os registros DNS locais criados na interface"
    assert not (included & owned_by_compose)
    unwanted = {k for k in env if k.startswith("SYNC_CONFIG_") and k not in {"SYNC_CONFIG_DNS", "SYNC_CONFIG_DNS_INCLUDE"}}
    assert not unwanted, f"não sincronize outras seções de config: {unwanted}"
    assert "SYNC_GRAVITY_DHCP_LEASES" not in env, "o DHCP não é do Pi-hole neste homelab"


def test_nebula_sync_points_primary_at_node_1_and_replica_at_node_2():
    env = _nebula()["environment"]
    assert env["PRIMARY"].startswith("http://${PIHOLE1_IP") and "|${PIHOLE1_PASSWORD" in env["PRIMARY"]
    assert env["REPLICAS"].startswith("http://${PIHOLE2_IP") and "|${PIHOLE2_PASSWORD" in env["REPLICAS"]
    assert len(env["CRON"].split()) == 5, "CRON em 5 campos"


def test_nebula_sync_is_isolated_and_pinned():
    svc = _nebula()
    assert re.fullmatch(r"ghcr\.io/lovelaze/nebula-sync:v\d+\.\d+\.\d+", svc["image"])
    assert "networks" not in svc, "rede padrão: não precisa da proxy-net nem do socket"
    assert "ports" not in svc and not svc.get("volumes")
    assert svc["cap_drop"] == ["ALL"]


def test_pihole_node_ips_in_env_examples_agree():
    s3, dns = parse_env(ROOT / ".env.example"), parse_env(ROOT / "stack-dns/.env.example")
    assert s3["PIHOLE1_IP"] == dns["NODE_IP"], "o exemplo do nó 1 deve bater com o PIHOLE1_IP do Servidor 3"
    assert s3["PIHOLE1_IP"] != s3["PIHOLE2_IP"]
