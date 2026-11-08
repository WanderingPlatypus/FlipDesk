# FlipDesk

Self-hosted tracker for what you buy and resell on Leboncoin, Vinted & co.
One Python file + one HTML page, SQLite storage, **zero dependencies** (only `python3`).

## What it tracks
- **Buy side:** platform, date, price paid, fees/shipping, parts/repair cost
- **Sell side:** platform, date, sale price, seller fees, shipping you paid
- **Status:** In stock → Listed (with asking price) → Sold / Kept
- **Dashboard:** net profit, revenue, margin on cost, money spent, capital tied up in stock, average days to sell, profit per month (12-month chart), breakdown per selling platform. Filter by all time / this year / this month / last 30 days.
- One-tap **"Vendu ✓"** button on unsold items (prefills today's date, the asking price and the platform)
- CSV export (opens in Excel), JSON backup / restore
- Works on phone (add it to your home screen)

Profit = sale price − seller fees − shipping you paid − (buy price + buy fees + repair cost)

## Install on Proxmox (LXC, recommended)

1. **Create a container** in the Proxmox UI (`https://192.168.1.48:8006`):
   *Create CT* → template **debian-13-standard** (12 works too) → 1 core, 256 MB RAM, 2 GB disk, network DHCP (or a static IP so the URL never changes). Unprivileged is fine.
2. **Install**, in the container console:
   ```bash
   apt update && apt install -y git
   git clone https://github.com/WanderingPlatypus/FlipDesk.git /root/flipdesk
   bash /root/flipdesk/install.sh
   ```
3. Open the URL printed at the end, e.g. `http://192.168.1.xx:8080`.

To **update** later: `cd /root/flipdesk && git pull && bash install.sh`. Your data and password are kept.

### Password (optional)
Edit `/etc/flipdesk.env`, uncomment `FLIPDESK_PASSWORD=...`, then `systemctl restart flipdesk`.
The browser will ask for a login: type anything as username, and the password.
Don't expose it to the internet without a reverse proxy + HTTPS (or use Tailscale/WireGuard to reach it from outside).

### Backups
- The whole CT is covered by Proxmox backups (Datacenter → Backup).
- Data file: `/var/lib/flipdesk/flipdesk.db`
- Or from the app: ⋯ → *Télécharger une sauvegarde* (JSON), restorable via ⋯ → *Restaurer*.

### Useful commands
```bash
systemctl status flipdesk
journalctl -u flipdesk -f
```

## Alternative: Docker
```bash
docker compose up -d --build     # → http://<host>:8080, data in ./data
```

## Run it anywhere (test)
```bash
python3 server.py                # → http://localhost:8080
```
Env vars: `FLIPDESK_PORT`, `FLIPDESK_HOST`, `FLIPDESK_DB`, `FLIPDESK_PASSWORD`.
