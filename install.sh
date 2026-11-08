#!/usr/bin/env bash
# Installe (ou met à jour) FlipDesk dans un conteneur LXC Debian/Ubuntu.
# À lancer en root DANS le conteneur, depuis un clone git du dépôt :
#   git clone https://github.com/WanderingPlatypus/FlipDesk.git /root/flipdesk
#   bash /root/flipdesk/install.sh [port]
# L'application est installée comme clone git dans /opt/flipdesk, ce qui permet
# ensuite de la mettre à jour depuis l'interface (⋯ → Mettre à jour le serveur).
set -euo pipefail

PORT="${1:-8080}"
SRC="$(cd "$(dirname "$0")" && pwd)"
APP=/opt/flipdesk
DATA=/var/lib/flipdesk
DEFAULT_REPO=https://github.com/WanderingPlatypus/FlipDesk.git

if [ "$(id -u)" -ne 0 ]; then echo "Lance ce script en root."; exit 1; fi

need=()
command -v python3 >/dev/null || need+=(python3)
command -v git >/dev/null || need+=(git)
if [ ${#need[@]} -gt 0 ]; then apt-get update && apt-get install -y "${need[@]}" ca-certificates; fi

REPO="$(git -C "$SRC" remote get-url origin 2>/dev/null || echo "$DEFAULT_REPO")"
BRANCH="$(git -C "$SRC" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"

id flipdesk >/dev/null 2>&1 || useradd --system --home "$DATA" --shell /usr/sbin/nologin flipdesk
mkdir -p "$DATA"
chown flipdesk:flipdesk "$DATA"

if [ -d "$APP/.git" ]; then
  echo "→ Mise à jour du clone existant"
  runuser -u flipdesk -- git -C "$APP" fetch --quiet origin
  runuser -u flipdesk -- git -C "$APP" merge --ff-only "origin/$BRANCH" || true
else
  echo "→ Installation de $REPO dans $APP"
  rm -rf "$APP"   # ancienne installation par copie (aucune donnée dedans)
  git clone --branch "$BRANCH" "$REPO" "$APP"
  chown -R flipdesk:flipdesk "$APP"
fi

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
After=network-online.target
Wants=network-online.target

[Service]
User=flipdesk
WorkingDirectory=$APP
EnvironmentFile=$ENVF
ExecStart=/usr/bin/python3 $APP/server.py
Restart=always
RestartSec=2
NoNewPrivileges=true
ProtectSystem=strict
PrivateTmp=true
ReadWritePaths=$DATA $APP

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable flipdesk >/dev/null
systemctl restart flipdesk
sleep 1
systemctl --no-pager --lines=0 status flipdesk || true

IP=$(hostname -I | awk '{print $1}')
P=$(grep -E '^FLIPDESK_PORT=' "$ENVF" | cut -d= -f2)
echo
echo "✅ FlipDesk tourne : http://$IP:${P:-$PORT}"
echo "   Données : $DATA/flipdesk.db   Config : $ENVF"
echo "   Mises à jour : depuis l'appli, menu ⋯ → Mettre à jour le serveur"
