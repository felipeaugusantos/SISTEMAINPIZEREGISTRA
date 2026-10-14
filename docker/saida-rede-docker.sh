#!/bin/sh
set -eu

# Reforço de rede do A09 (auditoria externa de 13/10/2026): impede que os containers do
# projeto alcancem serviços de metadata de nuvem (169.254.0.0/16) e redes privadas da
# infraestrutura. A internet pública continua liberada (SMTP, Stripe, Gemini, Receita, INPI...).
# É a segunda camada: a primeira é o código (app/verificacao_site.py).
#
# Uso (como root, na VPS, a partir de /opt/zeregistra):
#   sh docker/saida-rede-docker.sh aplicar    cria/atualiza as regras (idempotente)
#   sh docker/saida-rede-docker.sh remover    tira as regras (volta ao comportamento anterior)
#   sh docker/saida-rede-docker.sh status     mostra as regras e os contadores de bloqueio
#   sh docker/saida-rede-docker.sh instalar   aplica agora e reaplica a cada boot (systemd)
#
# Não derruba nem reinicia nenhum container; só mexe na cadeia ZEREGISTRA-SAIDA do iptables,
# ligada à cadeia DOCKER-USER (que o Docker respeita e não apaga).

PROJETO="${COMPOSE_PROJECT_NAME:-zeregistra}"
CADEIA="ZEREGISTRA-SAIDA"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
UNIDADE="zeregistra-saida-rede"

# Destinos que os containers NÃO devem alcançar (metadata, CGNAT e redes privadas).
BLOQUEADOS="169.254.0.0/16 100.64.0.0/10 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16"

exigir_root() {
  [ "$(id -u)" -eq 0 ] || { echo "Rode como root (sudo)." >&2; exit 1; }
  command -v iptables >/dev/null 2>&1 || { echo "iptables não encontrado." >&2; exit 1; }
}

sub_redes_do_projeto() {
  for rede in $(docker network ls -q --filter "label=com.docker.compose.project=$PROJETO"); do
    docker network inspect "$rede" --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}'
  done | tr ' ' '\n' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$' | sort -u
}

# Resolvedores de DNS do host (o DNS interno do Docker consulta por eles); sem isto, um
# resolvedor em IP privado ficaria bloqueado e nenhum container resolveria nomes.
resolvedores() {
  cat /etc/resolv.conf /run/systemd/resolve/resolv.conf 2>/dev/null \
    | awk '$1 == "nameserver" {print $2}' \
    | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | grep -v '^127\.' | sort -u || true
}

remover_ligacoes() {
  # Apaga toda regra de DOCKER-USER que salta para a nossa cadeia (uma por rede do projeto).
  iptables -S DOCKER-USER | grep -- "-j $CADEIA\$" | sed 's/^-A /-D /' | while read -r regra; do
    iptables $regra
  done
}

remover() {
  exigir_root
  iptables -S DOCKER-USER >/dev/null 2>&1 && remover_ligacoes
  if iptables -S "$CADEIA" >/dev/null 2>&1; then
    iptables -F "$CADEIA"
    iptables -X "$CADEIA"
  fi
  echo "Regras de saída removidas."
}

aplicar() {
  exigir_root
  SUBREDES="$(sub_redes_do_projeto)"
  if [ -z "$SUBREDES" ]; then
    echo "Nenhuma rede do projeto '$PROJETO' encontrada. Suba os serviços antes (docker compose up -d)." >&2
    exit 1
  fi
  iptables -N DOCKER-USER 2>/dev/null || true
  iptables -N "$CADEIA" 2>/dev/null || true
  iptables -F "$CADEIA"

  iptables -A "$CADEIA" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
  for ip in $(resolvedores); do
    iptables -A "$CADEIA" -d "$ip" -p udp --dport 53 -j RETURN
    iptables -A "$CADEIA" -d "$ip" -p tcp --dport 53 -j RETURN
  done
  # Tráfego entre os próprios containers do projeto (api -> db, redis, clamav...).
  for sub in $SUBREDES; do
    iptables -A "$CADEIA" -d "$sub" -j RETURN
  done
  for destino in $BLOQUEADOS; do
    iptables -A "$CADEIA" -d "$destino" -j REJECT --reject-with icmp-net-unreachable
  done
  iptables -A "$CADEIA" -j RETURN

  remover_ligacoes
  for sub in $SUBREDES; do
    iptables -I DOCKER-USER 1 -s "$sub" -j "$CADEIA"
  done
  echo "Regras de saída aplicadas às redes: $(echo $SUBREDES)"
}

status() {
  exigir_root
  if ! iptables -S "$CADEIA" >/dev/null 2>&1; then
    echo "Cadeia $CADEIA não existe: nenhuma restrição de saída ativa."
    exit 0
  fi
  echo "== DOCKER-USER =="
  iptables -L DOCKER-USER -n -v --line-numbers
  echo
  echo "== $CADEIA (a coluna pkts mostra quantos pacotes foram barrados) =="
  iptables -L "$CADEIA" -n -v --line-numbers
  atuais="$(sub_redes_do_projeto)"
  for sub in $atuais; do
    iptables -C DOCKER-USER -s "$sub" -j "$CADEIA" 2>/dev/null \
      || echo "ATENÇÃO: a rede $sub não está coberta; rode 'aplicar' de novo."
  done
}

instalar() {
  exigir_root
  aplicar
  cat > "/etc/systemd/system/$UNIDADE.service" <<EOF
[Unit]
Description=Zé Registra - restringe a saída de rede dos containers
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh $DIR/docker/saida-rede-docker.sh aplicar
ExecStop=/bin/sh $DIR/docker/saida-rede-docker.sh remover

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "$UNIDADE.service"
  echo "Unidade $UNIDADE instalada: as regras voltam sozinhas a cada boot."
}

case "${1:-}" in
  aplicar) aplicar ;;
  remover) remover ;;
  status) status ;;
  instalar) instalar ;;
  *) echo "Uso: sh $0 aplicar|remover|status|instalar" >&2; exit 2 ;;
esac
