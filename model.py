"""FlipDesk v2 data model: purchases, sales, and the links between them.

Cost allocation (same rule in the web page, see static/index.html):
each purchase's total cost (price + fees) is spread over the sales it is linked to,
weighted by sale price (sold) or asking price (listed); equal split if none has a
price. A sale's cost = its share of every linked purchase + its own repair costs.
"""
import re

SCHEMA = """
CREATE TABLE IF NOT EXISTS purchases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  category TEXT DEFAULT '',
  platform TEXT DEFAULT '',
  date TEXT DEFAULT '',
  price REAL DEFAULT 0,
  fees REAL DEFAULT 0,
  status TEXT DEFAULT 'stock',
  url TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  photo TEXT DEFAULT '',
  ext_ids TEXT DEFAULT '',
  created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS sales (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  category TEXT DEFAULT '',
  condition TEXT DEFAULT '',
  status TEXT DEFAULT 'listed',
  platform TEXT DEFAULT '',
  list_price REAL DEFAULT 0,
  list_date TEXT DEFAULT '',
  date TEXT DEFAULT '',
  price REAL DEFAULT 0,
  fees REAL DEFAULT 0,
  ship_cost REAL DEFAULT 0,
  repair_cost REAL DEFAULT 0,
  url TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  photo TEXT DEFAULT '',
  owner TEXT DEFAULT '',
  commission REAL DEFAULT 0,
  settled REAL DEFAULT 0,
  ext_ids TEXT DEFAULT '',
  created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS links (
  purchase_id INTEGER NOT NULL REFERENCES purchases(id) ON DELETE CASCADE,
  sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
  PRIMARY KEY (purchase_id, sale_id)
);
CREATE TABLE IF NOT EXISTS ignored (ext TEXT PRIMARY KEY);
"""

TXT, NUM = str, float
FIELDS = {
    "purchases": {"name": TXT, "category": TXT, "platform": TXT, "date": TXT, "price": NUM, "fees": NUM,
                  "status": TXT, "url": TXT, "notes": TXT, "photo": TXT},
    "sales": {"name": TXT, "category": TXT, "condition": TXT, "status": TXT, "platform": TXT,
              "list_price": NUM, "list_date": TXT, "date": TXT, "price": NUM, "fees": NUM,
              "ship_cost": NUM, "repair_cost": NUM, "url": TXT, "notes": TXT, "photo": TXT,
              "owner": TXT, "commission": NUM, "settled": NUM},
}
STATUSES = {"purchases": {"stock", "kept", "used"}, "sales": {"listed", "sold"}}
SALE_EXT = ("vinted:item:", "vinted:tx:", "lbc:ad:", "lbc:tx:")


# ---------- helpers ----------
def clean(table, payload, partial=False):
    out = {}
    for k, typ in FIELDS[table].items():
        if k not in payload:
            continue
        v = payload[k]
        if typ is NUM:
            if v in (None, ""):
                v = 0.0
            else:
                try:
                    v = round(float(str(v).replace(",", ".")), 2)
                except ValueError:
                    raise ValueError(f"Valeur invalide pour {k}")
        else:
            v = "" if v is None else str(v).strip()[:4000]
            if (k == "date" or k.endswith("_date")) and v and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                raise ValueError(f"Date invalide pour {k} (AAAA-MM-JJ)")
        out[k] = v
    if "status" in out and out["status"] not in STATUSES[table]:
        raise ValueError("Statut invalide")
    if not partial and not out.get("name"):
        raise ValueError("Le nom est obligatoire")
    if "name" in out and not out["name"]:
        raise ValueError("Le nom est obligatoire")
    return out


def rowd(r):
    d = {k: r[k] for k in r.keys()}
    if "ext_ids" in d:
        d["ext_ids"] = d["ext_ids"].split()
    return d


def fmt_ext(ids):
    ids = sorted(set(ids))
    return " " + " ".join(ids) + " " if ids else ""


def insert(c, table, row):
    cur = c.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
    return cur.lastrowid


def get(c, table, iid):
    r = c.execute(f"SELECT * FROM {table} WHERE id=?", (iid,)).fetchone()
    return rowd(r) if r else None


# ---------- CRUD ----------
def create(c, table, data, now):
    row = clean(table, data)
    row.setdefault("status", "stock" if table == "purchases" else "listed")
    row["created_at"] = row["updated_at"] = now
    iid = insert(c, table, row)
    # optional links created together with the record
    other = "sale_ids" if table == "purchases" else "purchase_ids"
    for oid in data.get(other) or []:
        if table == "purchases":
            link(c, iid, int(oid))
        else:
            link(c, int(oid), iid)
    return get(c, table, iid)


def update(c, table, iid, data, now):
    row = clean(table, data, partial=True)
    if not get(c, table, iid):
        return None
    row["updated_at"] = now
    c.execute(f"UPDATE {table} SET {','.join(k + '=?' for k in row)} WHERE id=?", list(row.values()) + [iid])
    return get(c, table, iid)


def delete(c, table, iid):
    r = c.execute(f"SELECT ext_ids FROM {table} WHERE id=?", (iid,)).fetchone()
    for e in (r["ext_ids"] or "").split() if r else []:
        c.execute("INSERT OR IGNORE INTO ignored (ext) VALUES (?)", (e,))  # never re-created by a sync
    c.execute(f"DELETE FROM links WHERE {'purchase_id' if table == 'purchases' else 'sale_id'}=?", (iid,))
    c.execute(f"DELETE FROM {table} WHERE id=?", (iid,))


def link(c, pid, sid):
    if not get(c, "purchases", pid) or not get(c, "sales", sid):
        raise ValueError("Achat ou vente introuvable")
    c.execute("INSERT OR IGNORE INTO links (purchase_id, sale_id) VALUES (?,?)", (pid, sid))


def unlink(c, pid, sid):
    c.execute("DELETE FROM links WHERE purchase_id=? AND sale_id=?", (pid, sid))


def dump(c):
    return {
        "purchases": [rowd(r) for r in c.execute("SELECT * FROM purchases ORDER BY date DESC, id DESC")],
        "sales": [rowd(r) for r in c.execute("SELECT * FROM sales ORDER BY COALESCE(NULLIF(date,''), NULLIF(list_date,''), created_at) DESC, id DESC")],
        "links": [[r["purchase_id"], r["sale_id"]] for r in c.execute("SELECT * FROM links")],
    }


# ---------- cost allocation (for CSV export) ----------
def allocate(data):
    sales = {s["id"]: s for s in data["sales"]}
    by_p = {}
    for pid, sid in data["links"]:
        by_p.setdefault(pid, []).append(sid)
    alloc = {sid: 0.0 for sid in sales}
    for p in data["purchases"]:
        sids = [s for s in by_p.get(p["id"], []) if s in sales]
        if not sids:
            continue
        total = (p["price"] or 0) + (p["fees"] or 0)
        w = [(sales[s]["price"] if sales[s]["status"] == "sold" else sales[s]["list_price"]) or 0 for s in sids]
        if sum(w) <= 0:
            w = [1] * len(sids)
        for s, wi in zip(sids, w):
            alloc[s] += total * wi / sum(w)
    return alloc


def export_rows(c, kind):
    data = dump(c)
    names_p = {p["id"]: p["name"] for p in data["purchases"]}
    names_s = {s["id"]: s["name"] for s in data["sales"]}
    if kind == "purchases":
        cols = ["id", "name", "category", "platform", "date", "price", "fees", "status", "ventes_liees", "notes"]
        rows = []
        for p in data["purchases"]:
            r = dict(p)
            r["ventes_liees"] = ", ".join(names_s[s] for pid, s in data["links"] if pid == p["id"])
            rows.append(r)
        return rows, cols
    alloc = allocate(data)
    cols = ["id", "name", "category", "status", "platform", "list_price", "date", "price", "fees", "ship_cost",
            "repair_cost", "cout_achat", "benefice", "achats_lies", "pour", "commission", "a_reverser", "reverse", "notes"]
    rows = []
    for s in data["sales"]:
        r = dict(s)
        r["cout_achat"] = round(alloc[s["id"]], 2)
        r["benefice"] = round(s["price"] - s["fees"] - s["ship_cost"] - s["repair_cost"] - alloc[s["id"]], 2) if s["status"] == "sold" else ""
        r["achats_lies"] = ", ".join(names_p[p] for p, sid in data["links"] if sid == s["id"])
        r["pour"] = s["owner"]
        r["a_reverser"] = round(s["price"] - s["fees"] - s["ship_cost"] - s["commission"], 2) if s["owner"] and s["status"] == "sold" else ""
        r["reverse"] = ("oui" if s["settled"] else "non") if s["owner"] else ""
        rows.append(r)
    return rows, cols


# ---------- v1 -> v2 conversion ----------
NOTE_EXT = [  # ids written in notes by the earlier one-shot imports
    (r"Vente Leboncoin n°(\d+)", "lbc:tx:"), (r"Transaction Leboncoin (\d+)", "lbc:tx:"),
    (r"Achat Leboncoin n°(\d+)", "lbc:buy:"), (r"Vente Vinted n°(\d+)", "vinted:tx:"),
    (r"Achat Vinted n°(\d+)", "vinted:buy:"),
]


def convert_v1(c, items, now):
    """v1 'items' (one row = purchase + sale) -> v2 purchases / sales / links.
    Items sharing a lot (parent_id or old lot name) get every purchase linked to every sale."""
    ids = {}
    groups = {}
    known = {it.get("id") for it in items}
    for it in items:
        g = lambda k, d=0: it.get(k) if it.get(k) not in (None, "") else d
        st = g("status", "stock")
        buy_total = float(g("buy_price")) + float(g("buy_fees"))
        repair = float(g("repair_cost"))
        has_sale = st in ("listed", "sold") or float(g("sell_price")) > 0 or float(g("list_price")) > 0
        bp = g("buy_platform", "")
        has_buy = buy_total > 0 or (bp and bp != "Déjà possédé") or st in ("stock", "kept", "parts") or not has_sale
        exts = set(it.get("ext_ids").split() if isinstance(it.get("ext_ids"), str) else it.get("ext_ids") or [])
        for rx, pre in NOTE_EXT:
            exts |= {pre + m for m in re.findall(rx, g("notes", ""))}
        pid = sid = None
        if has_buy:
            pid = insert(c, "purchases", {
                "name": g("name", "Article"), "category": g("category", ""),
                "platform": "" if bp == "Déjà possédé" else bp, "date": g("buy_date", ""),
                "price": float(g("buy_price")), "fees": float(g("buy_fees")) + (0 if has_sale else repair),
                "status": {"kept": "kept", "parts": "used"}.get(st, "stock"),
                "url": "" if has_sale else g("url", ""), "notes": g("notes", ""),
                "ext_ids": fmt_ext(e for e in exts if not e.startswith(SALE_EXT)),
                "created_at": g("created_at", now), "updated_at": now})
        if has_sale:
            sid = insert(c, "sales", {
                "name": g("name", "Article"), "category": g("category", ""), "condition": g("condition", ""),
                "status": "sold" if st == "sold" else "listed", "platform": g("sell_platform", ""),
                "list_price": float(g("list_price")), "list_date": g("list_date", ""), "date": g("sell_date", ""),
                "price": float(g("sell_price")), "fees": float(g("sell_fees")), "ship_cost": float(g("ship_cost")),
                "repair_cost": repair, "url": g("url", ""), "notes": g("notes", ""),
                "ext_ids": fmt_ext(e for e in exts if e.startswith(SALE_EXT)),
                "created_at": g("created_at", now), "updated_at": now})
        if pid and sid:
            link(c, pid, sid)
        ids[it.get("id")] = (pid, sid)
        root = it.get("parent_id") if it.get("parent_id") in known else None
        key = ("id", root) if root else (("lot", it["lot"]) if it.get("lot") else ("id", it.get("id")))
        groups.setdefault(key, []).append(it.get("id"))
    for key, members in groups.items():
        if key[0] == "id" and key[1] in ids and key[1] not in members:
            members.append(key[1])  # the lot's origin itself
    for members in groups.values():
        if len(members) < 2:
            continue
        ps = [ids[m][0] for m in members if m in ids and ids[m][0]]
        ss = [ids[m][1] for m in members if m in ids and ids[m][1]]
        for p in ps:
            for s in ss:
                link(c, p, s)
    return len(items)


def migrate(c):
    c.executescript(SCHEMA)
    for t in ("purchases", "sales"):  # columns added after 2.0
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({t})")}
        if "photo" not in cols:
            c.execute(f"ALTER TABLE {t} ADD COLUMN photo TEXT DEFAULT ''")
        if t == "sales":  # sales made for a friend (2.2)
            for col, typ in (("owner", "TEXT DEFAULT ''"), ("commission", "REAL DEFAULT 0"), ("settled", "REAL DEFAULT 0")):
                if col not in cols:
                    c.execute(f"ALTER TABLE sales ADD COLUMN {col} {typ}")
    has_v1 = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='items'").fetchone()
    if has_v1:
        items = [dict(r) for r in c.execute("SELECT * FROM items ORDER BY id")]
        # parent groups in v1 are by root id: rebuild them before converting
        convert_v1(c, items, items[0]["updated_at"] if items else "")
        c.execute("ALTER TABLE items RENAME TO items_v1_backup")


def import_backup(c, data, now):
    if isinstance(data, list):
        data = {"items": data}
    replace = bool(data.get("replace"))
    if "items" in data and not data.get("purchases") and not data.get("sales"):
        if not isinstance(data["items"], list):
            raise ValueError("Format de sauvegarde invalide")
        if replace:
            c.execute("DELETE FROM links"); c.execute("DELETE FROM sales"); c.execute("DELETE FROM purchases")
        return convert_v1(c, data["items"], now)
    if replace:
        c.execute("DELETE FROM links"); c.execute("DELETE FROM sales"); c.execute("DELETE FROM purchases")
    idmap = {"purchases": {}, "sales": {}}
    for table in ("purchases", "sales"):
        for r in data.get(table, []):
            row = clean(table, r)
            row.setdefault("status", "stock" if table == "purchases" else "listed")
            ext = r.get("ext_ids") or []
            row["ext_ids"] = fmt_ext(ext.split() if isinstance(ext, str) else ext)
            row["created_at"] = r.get("created_at") or now
            row["updated_at"] = now
            idmap[table][r.get("id")] = insert(c, table, row)
    for pid, sid in data.get("links", []):
        if pid in idmap["purchases"] and sid in idmap["sales"]:
            link(c, idmap["purchases"][pid], idmap["sales"][sid])
    return len(data.get("purchases", [])) + len(data.get("sales", []))
