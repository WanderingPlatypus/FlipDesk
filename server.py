#!/usr/bin/env python3
"""FlipDesk - suivi achats / reventes Leboncoin & Vinted.

Zero dependency: Python 3.8+ standard library + SQLite.
Config via environment variables:
  FLIPDESK_PORT      (default 8080)
  FLIPDESK_HOST      (default 0.0.0.0)
  FLIPDESK_DB        (default ./data/flipdesk.db)
  FLIPDESK_PASSWORD  (optional - enables HTTP Basic auth, any username)
"""
import base64
import csv
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get("FLIPDESK_PORT", "8080"))
HOST = os.environ.get("FLIPDESK_HOST", "0.0.0.0")
DB_PATH = Path(os.environ.get("FLIPDESK_DB", str(HERE / "data" / "flipdesk.db")))
PASSWORD = os.environ.get("FLIPDESK_PASSWORD", "")
STATIC = HERE / "static"

FIELDS = {
    # name: type
    "name": str, "category": str, "condition": str,
    "buy_platform": str, "buy_date": str, "buy_price": float, "buy_fees": float,
    "repair_cost": float,
    "status": str,
    "list_price": float, "list_date": str,
    "sell_platform": str, "sell_date": str, "sell_price": float,
    "sell_fees": float, "ship_cost": float,
    "url": str, "notes": str, "lot": str,
}
STATUSES = {"stock", "listed", "sold", "kept", "parts"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  category TEXT DEFAULT '',
  condition TEXT DEFAULT '',
  buy_platform TEXT DEFAULT '',
  buy_date TEXT DEFAULT '',
  buy_price REAL DEFAULT 0,
  buy_fees REAL DEFAULT 0,
  repair_cost REAL DEFAULT 0,
  status TEXT DEFAULT 'stock',
  list_price REAL DEFAULT 0,
  list_date TEXT DEFAULT '',
  sell_platform TEXT DEFAULT '',
  sell_date TEXT DEFAULT '',
  sell_price REAL DEFAULT 0,
  sell_fees REAL DEFAULT 0,
  ship_cost REAL DEFAULT 0,
  url TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  lot TEXT DEFAULT '',
  created_at TEXT,
  updated_at TEXT
);
"""

_lock = threading.Lock()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.executescript(SCHEMA)
        cols = {r[1] for r in c.execute("PRAGMA table_info(items)")}
        if "lot" not in cols:  # migration for databases created before lots existed
            c.execute("ALTER TABLE items ADD COLUMN lot TEXT DEFAULT ''")


def clean(payload, partial=False):
    out = {}
    for k, typ in FIELDS.items():
        if k not in payload:
            continue
        v = payload[k]
        if typ is float:
            if v in (None, ""):
                v = 0.0
            else:
                try:
                    v = round(float(str(v).replace(",", ".")), 2)
                except ValueError:
                    raise ValueError(f"Valeur invalide pour {k}")
        else:
            v = "" if v is None else str(v).strip()[:2000]
            if k.endswith("_date") and v and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                raise ValueError(f"Date invalide pour {k} (AAAA-MM-JJ)")
        out[k] = v
    if "status" in out and out["status"] not in STATUSES:
        raise ValueError("Statut invalide")
    if not partial and not out.get("name"):
        raise ValueError("Le nom est obligatoire")
    if "name" in out and not out["name"]:
        raise ValueError("Le nom est obligatoire")
    return out


# ---------- self-update (when installed as a git clone) ----------
def git(*args, timeout=90):
    r = subprocess.run(["git", "-C", str(HERE), *args], capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def git_supported():
    try:
        return (HERE / ".git").exists() and git("rev-parse", "HEAD")[0] == 0
    except (OSError, subprocess.SubprocessError):
        return False


def version_info():
    if not git_supported():
        return {"supported": False}
    _, out = git("log", "-1", "--format=%h|%cs|%s")
    h, d, msg = (out.split("|", 2) + ["", ""])[:3]
    return {"supported": True, "commit": h, "date": d, "message": msg}


def pending_updates():
    code, out = git("fetch", "--quiet")
    if code:
        raise RuntimeError("git fetch a échoué : " + out[-300:])
    _, log = git("log", "--format=%h %cs %s", "HEAD..@{u}")
    return [l for l in log.splitlines() if l.strip()]


def restart_soon():
    # Replace this process with a fresh one running the updated code (same PID,
    # so it works under systemd or when launched by hand).
    def go():
        time.sleep(0.8)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=go, daemon=True).start()


def now():
    return datetime.now().isoformat(timespec="seconds")


def row_dict(r):
    return {k: r[k] for k in r.keys()}


class Handler(BaseHTTPRequestHandler):
    server_version = "FlipDesk/1.0"

    def log_message(self, fmt, *args):
        pass  # quiet

    # ---------- helpers ----------
    def _auth_ok(self):
        if not PASSWORD:
            return True
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                _, pw = base64.b64decode(h[6:]).decode().split(":", 1)
                return pw == PASSWORD
            except Exception:
                return False
        return False

    def _send(self, code, body=b"", ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _err(self, code, msg):
        self._send(code, {"error": msg})

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 20 * 1024 * 1024:
            raise ValueError("Requête trop grosse")
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw.decode() or "{}")

    def _guard(self):
        if self._auth_ok():
            return True
        self._send(401, {"error": "auth"},
                   extra={"WWW-Authenticate": 'Basic realm="FlipDesk"'})
        return False

    def _item_id(self):
        m = re.fullmatch(r"/api/items/(\d+)", self.path.split("?")[0])
        return int(m.group(1)) if m else None

    # ---------- routes ----------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._guard():
            return
        path = self.path.split("?")[0]
        if path == "/api/health":
            return self._send(200, {"ok": True})
        if path == "/api/version":
            return self._send(200, version_info())
        if path == "/api/items":
            with db() as c:
                rows = c.execute("SELECT * FROM items ORDER BY COALESCE(NULLIF(sell_date,''), NULLIF(buy_date,''), created_at) DESC, id DESC").fetchall()
            return self._send(200, [row_dict(r) for r in rows])
        if path == "/api/backup.json":
            with db() as c:
                rows = [row_dict(r) for r in c.execute("SELECT * FROM items ORDER BY id")]
            fname = f"flipdesk-backup-{datetime.now():%Y%m%d}.json"
            return self._send(200, {"version": 1, "exported_at": now(), "items": rows},
                              extra={"Content-Disposition": f'attachment; filename="{fname}"'})
        if path == "/api/export.csv":
            with db() as c:
                rows = [row_dict(r) for r in c.execute("SELECT * FROM items ORDER BY id")]
            buf = io.StringIO()
            cols = ["id"] + list(FIELDS.keys()) + ["profit"]
            w = csv.writer(buf, delimiter=";")
            w.writerow(cols)
            for r in rows:
                cost = (r["buy_price"] or 0) + (r["buy_fees"] or 0) + (r["repair_cost"] or 0)
                profit = ""
                if r["status"] == "sold":
                    profit = round((r["sell_price"] or 0) - (r["sell_fees"] or 0) - (r["ship_cost"] or 0) - cost, 2)
                vals = [r.get(k, "") for k in cols[:-1]] + [profit]
                w.writerow([str(v).replace(".", ",") if isinstance(v, float) else v for v in vals])
            data = "﻿" + buf.getvalue()  # BOM so Excel reads accents
            fname = f"flipdesk-{datetime.now():%Y%m%d}.csv"
            return self._send(200, data, "text/csv; charset=utf-8",
                              {"Content-Disposition": f'attachment; filename="{fname}"'})
        if path.startswith("/api/"):
            return self._err(404, "not found")
        # static
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        f = (STATIC / rel).resolve()
        if STATIC not in f.parents and f != STATIC / "index.html" or not f.is_file():
            f = STATIC / "index.html"
        ctype = {
            ".html": "text/html; charset=utf-8", ".js": "text/javascript",
            ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png",
            ".json": "application/json", ".webmanifest": "application/manifest+json",
        }.get(f.suffix, "application/octet-stream")
        return self._send(200, f.read_bytes(), ctype)

    def do_POST(self):
        if not self._guard():
            return
        path = self.path.split("?")[0]
        try:
            data = self._json_body()
            if path == "/api/items":
                item = clean(data)
                item.setdefault("status", "stock")
                item["created_at"] = item["updated_at"] = now()
                with _lock, db() as c:
                    cols = ",".join(item)
                    cur = c.execute(f"INSERT INTO items ({cols}) VALUES ({','.join('?' * len(item))})",
                                    list(item.values()))
                    r = c.execute("SELECT * FROM items WHERE id=?", (cur.lastrowid,)).fetchone()
                return self._send(201, row_dict(r))
            if path in ("/api/update/check", "/api/update"):
                if not git_supported():
                    return self._err(400, "Mise à jour automatique indisponible : FlipDesk n'est pas installé depuis git.")
                try:
                    pend = pending_updates()
                    if path == "/api/update/check":
                        return self._send(200, {"pending": pend, **version_info()})
                    if not pend:
                        return self._send(200, {"updated": False, "restarting": False})
                    code, out = git("merge", "--ff-only", "@{u}")
                    if code:
                        return self._err(500, "git a échoué : " + out[-400:])
                except (RuntimeError, subprocess.SubprocessError) as e:
                    return self._err(500, str(e))
                restart_soon()
                return self._send(200, {"updated": True, "restarting": True, "applied": pend})
            if path == "/api/import":
                items = data.get("items", data) if isinstance(data, dict) else data
                replace = isinstance(data, dict) and data.get("replace")
                if not isinstance(items, list):
                    raise ValueError("Format de sauvegarde invalide")
                cleaned = []
                for it in items:
                    ci = clean(it)
                    ci.setdefault("status", "stock")
                    ci["created_at"] = it.get("created_at") or now()
                    ci["updated_at"] = now()
                    cleaned.append(ci)
                with _lock, db() as c:
                    if replace:
                        c.execute("DELETE FROM items")
                    for ci in cleaned:
                        c.execute(f"INSERT INTO items ({','.join(ci)}) VALUES ({','.join('?' * len(ci))})",
                                  list(ci.values()))
                return self._send(200, {"imported": len(cleaned)})
            return self._err(404, "not found")
        except (ValueError, json.JSONDecodeError) as e:
            return self._err(400, str(e))

    def do_PUT(self):
        if not self._guard():
            return
        iid = self._item_id()
        if iid is None:
            return self._err(404, "not found")
        try:
            item = clean(self._json_body(), partial=True)
        except (ValueError, json.JSONDecodeError) as e:
            return self._err(400, str(e))
        item["updated_at"] = now()
        with _lock, db() as c:
            sets = ",".join(f"{k}=?" for k in item)
            cur = c.execute(f"UPDATE items SET {sets} WHERE id=?", list(item.values()) + [iid])
            if cur.rowcount == 0:
                return self._err(404, "not found")
            r = c.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
        return self._send(200, row_dict(r))

    def do_DELETE(self):
        if not self._guard():
            return
        iid = self._item_id()
        if iid is None:
            return self._err(404, "not found")
        with _lock, db() as c:
            c.execute("DELETE FROM items WHERE id=?", (iid,))
        return self._send(200, {"deleted": iid})


def main():
    init_db()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"FlipDesk sur http://{HOST}:{PORT}  (db: {DB_PATH}){'  [mot de passe actif]' if PASSWORD else ''}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
