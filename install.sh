#!/usr/bin/env bash
# Installe FlipDesk dans un conteneur LXC Debian/Ubuntu (à lancer en root DANS le conteneur).
# Usage : bash install.sh [port]
set -euo pipefail

PORT="${1:-8080}"
SRC="$(cd "$(dirname "$0")" && pwd)"
APP=/opt/flipdesk
DATA=/var/lib/flipdesk

if [ "$(id -u)" -ne 0 ]; then echo "Lance ce script en root."; exit 1; fi

command -v python3 >/dev/null || { apt-get update && apt-get install -y python3; }

id flipdesk >/dev/null 2>&1 || useradd --system --home "$DATA" --shell /usr/sbin/nologin flipdesk
mkdir -p "$APP/static" "$DATA"
install -m 644 "$SRC/server.py" "$APP/server.py"
install -m 644 "$SRC/static/index.html" "$APP/static/index.html"
chown -R flipdesk:flipdesk "$DATA"

# Conserve un mot de passe déjà configuré lors d'une mise à jour
ENVF=/etc/flipdesk.env
if [ ! -f "$ENVF" ]; then
  cat > "$ENVF" <<EOF
FLIPDESK_PORT=$PORT
FLIPDESK_DB=$DATA/flipdesk.db
# Décommente pour protéger l'accès (n'importe quel identifiant, ce mot de passe) :
#FLIPDESK_PASSWORD=change-moi
EOF
  chmod 600 "$ENVF"
fi

cat > /etc/systemd/system/flipdesk.service <<EOF
[Unit]
Description=FlipDesk - suivi achats/ventes Leboncoin & Vinted
After=network.target

[Service]
User=flipdesk
EnvironmentFile=$ENVF
ExecStart=/usr/bin/python3 $APP/server.py
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$DATA

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now flipdesk
systemctl restart flipdesk
sleep 1
systemctl --no-pager --lines=0 status flipdesk || true

IP=$(hostname -I | awk '{print $1}')
P=$(grep -E '^FLIPDESK_PORT=' "$ENVF" | cut -d= -f2)
echo
echo "✅ FlipDesk tourne : http://$IP:${P:-$PORT}"
echo "   Données : $DATA/flipdesk.db   Config : $ENVF"
