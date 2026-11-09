#!/usr/bin/env python3
"""FlipDesk v2 - suivi achats / reventes Leboncoin & Vinted.

Data model: purchases (achats) and sales (ventes / annonces) are separate records,
connected by links. One purchase can feed several sales (a lot), one sale can use
several purchases (parts). Each purchase's cost is split over its linked sales.

Zero dependency: Python 3.8+ standard library + SQLite.
Config via environment variables:
  FLIPDESK_PORT      (default 8080)
  FLIPDESK_HOST      (default 0.0.0.0)
  FLIPDESK_DB        (default ./data/flipdesk.db)
  FLIPDESK_PASSWORD  (optional - enables HTTP Basic auth, any username)
"""
import base64
import hashlib
import urllib.request
import zipfile
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

import model
import sync

HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get("FLIPDESK_PORT", "8080"))
HOST = os.environ.get("FLIPDESK_HOST", "0.0.0.0")
DB_PATH = Path(os.environ.get("FLIPDESK_DB", str(HERE / "data" / "flipdesk.db")))
PASSWORD = os.environ.get("FLIPDESK_PASSWORD", "")
STATIC = HERE / "static"
IMG = DB_PATH.parent / "img"   # photos: uploads and cached copies of listing photos
EXT_DIR = HERE / "extension"
SIGNED_XPI = DB_PATH.parent / "flipdesk-sync.xpi"   # signed by Mozilla, uploaded from the dashboard
BUNDLED_XPI = HERE / "signed" / "flipdesk-sync.xpi"  # signed version shipped with FlipDesk
IMG_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
VERSION = "2.4"

_lock = threading.Lock()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as c:
        model.migrate(c)


def cached_image(url):
    """Local copy of a listing photo, so it still shows once the listing is gone."""
    if not re.match(r"^https://[\w.-]+/", url):
        return None
    key = hashlib.sha1(url.encode()).hexdigest()
    for ext in IMG_TYPES.values():
        f = IMG / f"c_{key}{ext}"
        if f.exists():
            return f
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (FlipDesk)"})
    with urllib.request.urlopen(req, timeout=10) as r:
        ctype = r.headers.get_content_type()
        if ctype not in IMG_TYPES:
            return None
        data = r.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        return None
    IMG.mkdir(parents=True, exist_ok=True)
    f = IMG / f"c_{key}{IMG_TYPES[ctype]}"
    f.write_bytes(data)
    return f


def xpi_version(data_or_path):
    with zipfile.ZipFile(data_or_path if isinstance(data_or_path, Path) else io.BytesIO(data_or_path)) as z:
        names = set(z.namelist())
        m = json.loads(z.read("manifest.json"))
        signed = any(n.startswith("META-INF/") for n in names)
    gid = m.get("browser_specific_settings", {}).get("gecko", {}).get("id", "")
    return m.get("version", ""), signed, gid


def extension_zip(kind="firefox"):
    """Firefox: extension/ as is. Chrome: the shared files + extension/chrome/ (MV3
    manifest, service worker, PNG icons) at the root of the zip."""
    files = {}
    for f in sorted(EXT_DIR.rglob("*")):
        rel = f.relative_to(EXT_DIR).as_posix()
        if not f.is_file() or f.name.startswith(".") or rel.startswith("chrome/"):
            continue
        if kind == "chrome" and rel in ("manifest.json", "background.js"):
            continue
        files[rel] = f
    if kind == "chrome":
        for f in sorted((EXT_DIR / "chrome").rglob("*")):
            if f.is_file() and not f.name.startswith("."):
                files[f.relative_to(EXT_DIR / "chrome").as_posix()] = f
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, f in sorted(files.items()):
            z.write(f, rel)
    return buf.getvalue()


def vkey(v):
    return tuple(int(x) if x.isdigit() else 0 for x in (v or "0").split("."))


def best_signed():
    """Newest signed .xpi available: uploaded from the dashboard, or shipped with FlipDesk."""
    found = []
    for f in (SIGNED_XPI, BUNDLED_XPI):
        if f.exists():
            try:
                found.append((vkey(xpi_version(f)[0]), f))
            except (zipfile.BadZipFile, KeyError, ValueError):
                pass
    return max(found)[1] if found else None


def extension_info():
    src = ""
    if (EXT_DIR / "manifest.json").exists():
        src = json.loads((EXT_DIR / "manifest.json").read_text()).get("version", "")
    chrome = ""
    if (EXT_DIR / "chrome" / "manifest.json").exists():
        chrome = json.loads((EXT_DIR / "chrome" / "manifest.json").read_text()).get("version", "")
    f = best_signed()
    return {"source_version": src, "signed_version": xpi_version(f)[0] if f else None, "chrome_version": chrome}


def now():
    return datetime.now().isoformat(timespec="seconds")


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
        return {"supported": False, "version": VERSION}
    _, out = git("log", "-1", "--format=%h|%cs|%s")
    h, d, msg = (out.split("|", 2) + ["", ""])[:3]
    return {"supported": True, "version": VERSION, "commit": h, "date": d, "message": msg}


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


ROUTE = re.compile(r"^/api/(purchases|sales)(?:/(\d+))?$")


class Handler(BaseHTTPRequestHandler):
    server_version = "FlipDesk/" + VERSION

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
        if "Cache-Control" not in (extra or {}):
            self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
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
        self._send(401, {"error": "auth"}, extra={"WWW-Authenticate": 'Basic realm="FlipDesk"'})
        return False

    def _path(self):
        return self.path.split("?")[0]

    def _query(self):
        from urllib.parse import parse_qs
        return {k: v[0] for k, v in parse_qs(self.path.partition("?")[2]).items()}

    # ---------- routes ----------
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._guard():
            return
        path = self._path()
        if path == "/api/health":
            return self._send(200, {"ok": True, "version": VERSION})
        if path == "/api/version":
            return self._send(200, version_info())
        if path == "/api/img":
            url = self._query().get("u", "")
            try:
                f = cached_image(url)
            except Exception:
                f = None
            if not f:
                return self._err(404, "image indisponible")
            return self._send(200, f.read_bytes(), "image/" + {"jpg": "jpeg"}.get(f.suffix[1:], f.suffix[1:]),
                              {"Cache-Control": "max-age=31536000, immutable"})
        if path.startswith("/img/"):
            f = (IMG / path[5:]).resolve()
            if IMG.resolve() not in f.parents or not f.is_file():
                return self._err(404, "not found")
            return self._send(200, f.read_bytes(), "image/" + {"jpg": "jpeg"}.get(f.suffix[1:], f.suffix[1:]),
                              {"Cache-Control": "max-age=31536000, immutable"})
        if path == "/api/extension":
            return self._send(200, extension_info())
        if path == "/extension/flipdesk-sync.zip":
            return self._send(200, extension_zip(), "application/zip",
                              {"Content-Disposition": 'attachment; filename="flipdesk-sync.zip"'})
        if path == "/extension/flipdesk-sync-chrome.zip":
            return self._send(200, extension_zip("chrome"), "application/zip",
                              {"Content-Disposition": 'attachment; filename="flipdesk-sync-chrome.zip"'})
        if path == "/extension/flipdesk-sync.xpi":
            f = best_signed()
            if not f:
                return self._err(404, "Pas encore de version signée")
            return self._send(200, f.read_bytes(), "application/x-xpinstall",
                              {"Content-Disposition": 'attachment; filename="flipdesk-sync.xpi"'})
        if path == "/api/data":
            with db() as c:
                return self._send(200, model.dump(c))
        if path == "/api/backup.json":
            with db() as c:
                data = {"version": 2, "exported_at": now(), **model.dump(c)}
            fname = f"flipdesk-backup-{datetime.now():%Y%m%d}.json"
            return self._send(200, data, extra={"Content-Disposition": f'attachment; filename="{fname}"'})
        if path == "/api/export.csv":
            kind = self._query().get("type", "sales")
            with db() as c:
                rows, cols = model.export_rows(c, kind)
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=";")
            w.writerow(cols)
            for r in rows:
                w.writerow([str(r[k]).replace(".", ",") if isinstance(r[k], float) else r[k] for k in cols])
            fname = f"flipdesk-{'achats' if kind == 'purchases' else 'ventes'}-{datetime.now():%Y%m%d}.csv"
            return self._send(200, "﻿" + buf.getvalue(), "text/csv; charset=utf-8",
                              {"Content-Disposition": f'attachment; filename="{fname}"'})
        if path.startswith("/api/"):
            return self._err(404, "not found")
        # static
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        f = (STATIC / rel).resolve()
        if STATIC not in f.parents or not f.is_file():
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
        path = self._path()
        if path == "/api/extension/upload":  # raw body: the .xpi signed by addons.mozilla.org
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n <= 10 * 1024 * 1024:
                return self._err(400, "Fichier .xpi manquant ou trop gros")
            data = self.rfile.read(n)
            try:
                ver, signed, gid = xpi_version(data)
            except (zipfile.BadZipFile, KeyError, ValueError):
                return self._err(400, "Ce n'est pas une extension Firefox (.xpi)")
            if not gid.startswith("flipdesk-sync@"):
                return self._err(400, "Ce fichier n'est pas l'extension FlipDesk Sync")
            if not signed:
                return self._err(400, "Ce fichier n'est pas signé : signe-le d'abord sur addons.mozilla.org")
            SIGNED_XPI.parent.mkdir(parents=True, exist_ok=True)
            SIGNED_XPI.write_bytes(data)
            return self._send(201, {"signed_version": ver})
        if path == "/api/upload":  # raw image body -> /img/<name>
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            n = int(self.headers.get("Content-Length") or 0)
            if ctype not in IMG_TYPES or not 0 < n <= 15 * 1024 * 1024:
                return self._err(400, "Image JPEG, PNG, WebP ou GIF de 15 Mo maximum")
            data = self.rfile.read(n)
            IMG.mkdir(parents=True, exist_ok=True)
            name = "u_" + hashlib.sha1(data).hexdigest()[:20] + IMG_TYPES[ctype]
            (IMG / name).write_bytes(data)
            return self._send(201, {"url": "/img/" + name})
        try:
            data = self._json_body()
            m = ROUTE.match(path)
            if m and not m.group(2):
                with _lock, db() as c:
                    row = model.create(c, m.group(1), data, now())
                return self._send(201, row)
            if path in ("/api/link", "/api/unlink"):
                with _lock, db() as c:
                    (model.link if path == "/api/link" else model.unlink)(c, int(data["purchase_id"]), int(data["sale_id"]))
                return self._send(200, {"ok": True})
            if path == "/api/import":
                with _lock, db() as c:
                    n = model.import_backup(c, data, now())
                return self._send(200, {"imported": n})
            if path == "/api/sync":
                with _lock, db() as c:
                    rep = sync.run(c, data, now())
                return self._send(200, rep)
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
            return self._err(404, "not found")
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            return self._err(400, str(e) if isinstance(e, ValueError) else f"Requête invalide : {e}")

    def do_PUT(self):
        if not self._guard():
            return
        m = ROUTE.match(self._path())
        if not m or not m.group(2):
            return self._err(404, "not found")
        try:
            data = self._json_body()
            with _lock, db() as c:
                row = model.update(c, m.group(1), int(m.group(2)), data, now())
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            return self._err(400, str(e))
        if row is None:
            return self._err(404, "not found")
        return self._send(200, row)

    def do_DELETE(self):
        if not self._guard():
            return
        m = ROUTE.match(self._path())
        if not m or not m.group(2):
            return self._err(404, "not found")
        with _lock, db() as c:
            model.delete(c, m.group(1), int(m.group(2)))
        return self._send(200, {"deleted": int(m.group(2))})


def main():
    init_db()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"FlipDesk {VERSION} sur http://{HOST}:{PORT}  (db: {DB_PATH}){'  [mot de passe actif]' if PASSWORD else ''}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
