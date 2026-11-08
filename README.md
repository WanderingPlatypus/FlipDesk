# FlipDesk v2

Self-hosted tracker for what you buy and resell on Leboncoin, Vinted & co.
One Python file + one HTML page, SQLite storage, **zero dependencies** (only `python3`).

## What it tracks
FlipDesk v2 keeps **purchases** (Achats) and **sales** (Ventes / annonces) as separate records, **linked** together:
- one purchase → several sales (a lot you split), several purchases → one sale (parts), or simply 1 ↔ 1
- each purchase's cost is spread over its linked sales (by sale price, or asking price while listed), so every sale shows its real profit and every purchase shows what it brought back
- 🏷️ **Ventes** view: listed / sold, the purchases each one came from, *Vendu ✓* in one click
- 🛒 **Achats** view: in stock / resold / used / kept, what each purchase became, *➕ En vente* to create the linked listing
- 💡 **link suggestions** when a sale and a purchase clearly look like the same thing (one click)
- **Dashboard:** net profit, revenue, margin on linked sales, money spent, capital tied up (unsold purchases + listings), average days to sell, profit per month, per selling platform; period filter
- CSV exports (sales with cost & profit, purchases), JSON backup / restore (v1 backups are converted on import)
- One-click server update from the ⋯ menu; works on phone

Sale profit = sale price − seller fees − shipping you paid − repair costs − its share of the linked purchases

**Upgrading from v1** is automatic: on first start, every v1 item becomes a purchase and/or a sale, linked together, and lots become links. Totals stay identical. The old table is kept in the database as `items_v1_backup`.

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

To **update** later: in the app, **⋯ → Mettre à jour le serveur**. It checks GitHub, lists the new changes, then pulls and restarts by itself. Your data and password are kept.
(The app lives as a git clone in `/opt/flipdesk`. If `install.sh` itself changes, rerun it: `cd /root/flipdesk && git pull && bash install.sh`.)

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

## Firefox extension (auto-sync Vinted & Leboncoin)
The `extension/` folder is a Firefox add-on that sends your listings, sales and purchases to FlipDesk:
- **Vinted**: syncs by itself when you open vinted.fr (at most every 10 min).
- **Leboncoin**: syncs when you open *Mes annonces* / *Mes transactions* (it reads what the page loads).
- **Synchroniser tout** button in the add-on popup opens those pages in background tabs and closes them.
- Never duplicates: every item remembers its Vinted/Leboncoin ids; an item listed on both sites stays one item; a sale turns the listing into *Vendu*; deleted items are never re-created. It never overwrites your costs, lots or notes.
- Warns you when something sold on one site is still online on the other.

Install: build `flipdesk-sync.zip` (zip the *contents* of `extension/`), then either
- **permanent**: sign it for free at addons.mozilla.org → Developer Hub → *Submit a new add-on* → **On your own** → upload the zip → install the signed `.xpi` it gives you;
- **quick test**: `about:debugging#/runtime/this-firefox` → *Load Temporary Add-on* → pick `extension/manifest.json` (removed when Firefox restarts).

Then open the add-on's settings and enter your FlipDesk address (e.g. `http://192.168.1.50:8080`).

## Alternative: Docker
```bash
docker compose up -d --build     # → http://<host>:8080, data in ./data
```

## Run it anywhere (test)
```bash
python3 server.py                # → http://localhost:8080
```
Env vars: `FLIPDESK_PORT`, `FLIPDESK_HOST`, `FLIPDESK_DB`, `FLIPDESK_PASSWORD`.
