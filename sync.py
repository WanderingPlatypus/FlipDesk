"""Sync of listings / sales / purchases pushed by the FlipDesk browser extension (v2).

Sales (table sales) carry the ids of their listings and transactions:
  vinted:item:<id>  lbc:ad:<list_id>   (listings)     vinted:tx:<id>  lbc:tx:<id>   (sales)
Purchases (table purchases) carry vinted:buy:<id> / lbc:buy:<id>.
An external id is never imported twice, ids of deleted records are remembered
(table ignored), and the sync never touches what the user typed (costs, links, notes).
"""
import re
import unicodedata
from datetime import date

from model import fmt_ext

STOP = {"de", "du", "des", "le", "la", "les", "l", "d", "un", "une", "et", "avec", "pour",
        "en", "a", "au", "the", "and", "with", "for", "tres", "bon", "etat", "neuf"}
PREFIX = {"Vinted": "vinted", "Leboncoin": "lbc"}


def tokens(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return {t for t in re.split(r"[^a-z0-9]+", s) if t and t not in STOP}


def title_match(a, b):
    A, B = tokens(a), tokens(b)
    if not A or not B:
        return False
    if A == B:
        return True
    small, big = (A, B) if len(A) <= len(B) else (B, A)
    return len(small) >= 2 and small <= big


def close(a, b, tol):
    return a > 0 and b > 0 and abs(a - b) <= tol * max(a, b)


def pick(rows, tiers):
    """First tier holding exactly one row wins; a tier with several rows is ambiguous."""
    for t in tiers:
        hit = [r for r in rows if t(r)]
        if len(hit) == 1:
            return hit[0]
        if len(hit) > 1:
            return None
    return None


def exts(row):
    return set((row["ext_ids"] or "").split())


def find(c, table, ext):
    return c.execute(f"SELECT * FROM {table} WHERE ext_ids LIKE ?", (f"% {ext} %",)).fetchone()


def add_ext(c, table, iid, ext):
    row = c.execute(f"SELECT ext_ids FROM {table} WHERE id=?", (iid,)).fetchone()
    c.execute(f"UPDATE {table} SET ext_ids=? WHERE id=?", (fmt_ext(set((row["ext_ids"] or "").split()) | {ext}), iid))


def ignored(c, ext):
    return c.execute("SELECT 1 FROM ignored WHERE ext=?", (ext,)).fetchone() is not None


def upd(c, table, iid, now, **kw):
    if kw:
        kw["updated_at"] = now
        c.execute(f"UPDATE {table} SET {','.join(k + '=?' for k in kw)} WHERE id=?", list(kw.values()) + [iid])


def new(c, table, now, ext, **kw):
    kw["created_at"] = kw["updated_at"] = now
    kw["ext_ids"] = fmt_ext([ext])
    c.execute(f"INSERT INTO {table} ({','.join(kw)}) VALUES ({','.join('?' * len(kw))})", list(kw.values()))


def run(c, payload, now):
    plat = payload.get("platform")
    if plat not in PREFIX:
        raise ValueError("Plateforme inconnue")
    pre = PREFIX[plat]
    other = [p for p in PREFIX.values() if p != pre][0]
    listing_pre = (pre + ":item:", pre + ":ad:")
    today = date.today().isoformat()
    rep = {"platform": plat, "created": [], "updated": [], "sold": [], "warnings": []}
    listed = lambda: c.execute("SELECT * FROM sales WHERE status='listed'").fetchall()

    # ---------- listings ----------
    for L in payload.get("listings", []):
        ext, price = L["ext"], float(L.get("price") or 0)
        if ignored(c, ext):
            continue
        s = find(c, "sales", ext)
        if s:
            if s["status"] == "listed":
                ch = {}
                both = any(e.startswith((other + ":item:", other + ":ad:")) for e in exts(s))
                if price and abs(price - (s["list_price"] or 0)) > 0.001 and (not both or not s["list_price"]):
                    ch["list_price"] = price
                if not s["url"] and L.get("url"):
                    ch["url"] = L["url"]
                if not s["photo"] and L.get("photo"):
                    ch["photo"] = L["photo"]
                if ch:
                    upd(c, "sales", s["id"], now, **ch)
                    rep["updated"].append(s["name"])
            elif L.get("active") and s["platform"] != plat:
                rep["warnings"].append(f"« {s['name']} » est vendu sur {s['platform']} mais toujours en ligne sur {plat}")
            continue
        if L.get("sold"):
            continue  # an already sold listing we never saw: its sale will create it
        # same thing already listed (imported, or listed on the other site)?
        cands = [r for r in listed() if title_match(r["name"], L["title"]) and not any(e.startswith(listing_pre) for e in exts(r))]
        r = pick(cands, [lambda r: abs((r["list_price"] or 0) - price) < .01,
                         lambda r: close(r["list_price"] or 0, price, .15),
                         lambda r: not r["list_price"]])
        if r is not None:
            add_ext(c, "sales", r["id"], ext)
            ch = {"list_price": price} if not r["list_price"] and price else {}
            if not r["url"] and L.get("url"):
                ch["url"] = L["url"]
            if not r["photo"] and L.get("photo"):
                ch["photo"] = L["photo"]
            upd(c, "sales", r["id"], now, **ch)
            rep["updated"].append(r["name"])
            continue
        # maybe already sold on the other site but still online here
        sold = [x for x in c.execute("SELECT * FROM sales WHERE status='sold' AND platform!=? AND date>=date('now','-60 day')", (plat,))
                if title_match(x["name"], L["title"]) and close(x["list_price"] or x["price"] or 0, price, .15)]
        if len(sold) == 1:
            add_ext(c, "sales", sold[0]["id"], ext)
            if L.get("active"):
                rep["warnings"].append(f"« {sold[0]['name']} » est vendu sur {sold[0]['platform']} mais toujours en ligne sur {plat}")
            continue
        new(c, "sales", now, ext, name=L["title"], status="listed", platform=plat, list_price=price,
            list_date=L.get("date") or today, url=L.get("url") or "", condition=L.get("condition") or "",
            photo=L.get("photo") or "",
            notes=f"Annonce {plat} ajoutée par l'extension")
        rep["created"].append(L["title"])

    # ---------- sales ----------
    for S in payload.get("sales", []):
        ext, price = S["ext"], float(S.get("price") or 0)
        if ignored(c, ext):
            continue
        s = find(c, "sales", ext)
        if S.get("cancelled"):
            if s and s["status"] == "sold" and s["platform"] == plat:
                upd(c, "sales", s["id"], now, status="listed", date="", price=0)
                rep["updated"].append(s["name"] + " (vente annulée)")
            continue
        if s and not s["photo"] and S.get("photo"):
            upd(c, "sales", s["id"], now, photo=S["photo"])
        if s:
            if s["status"] != "sold":
                upd(c, "sales", s["id"], now, status="sold", platform=plat, date=S.get("date") or today, price=price)
                rep["sold"].append(s["name"])
            elif s["platform"] == plat and price and abs(price - (s["price"] or 0)) > 0.001:
                upd(c, "sales", s["id"], now, price=price)
                rep["updated"].append(s["name"])
            continue
        target = find(c, "sales", S["item_ext"]) if S.get("item_ext") else None
        if not target:
            cands = [r for r in listed() if title_match(r["name"], S["title"])]
            mine = [r for r in cands if any(e.startswith(pre + ":") for e in exts(r))] or cands
            target = pick(mine, [lambda r: abs((r["list_price"] or 0) - price) < .01,
                                 lambda r: close(r["list_price"] or 0, price, .35),
                                 lambda r: not r["list_price"]])
        if target and target["status"] != "sold":
            add_ext(c, "sales", target["id"], ext)
            upd(c, "sales", target["id"], now, status="sold", platform=plat, date=S.get("date") or today, price=price,
                **({"photo": S["photo"]} if S.get("photo") and not target["photo"] else {}))
            rep["sold"].append(target["name"])
            if any(e.startswith(other + ":") for e in exts(target)):
                rep["warnings"].append(f"« {target['name']} » vendu sur {plat} : pense à retirer l'annonce de l'autre site")
            continue
        new(c, "sales", now, ext, name=S["title"], status="sold", platform=plat,
            date=S.get("date") or today, price=price, photo=S.get("photo") or "",
            notes=f"Vente {plat} ajoutée par l'extension")
        rep["sold"].append(S["title"])

    # ---------- purchases ----------
    for P in payload.get("purchases", []):
        ext = P["ext"]
        if P.get("cancelled") or ignored(c, ext):
            continue
        known = find(c, "purchases", ext)
        if known:
            if not known["photo"] and P.get("photo"):
                upd(c, "purchases", known["id"], now, photo=P["photo"])
            continue
        new(c, "purchases", now, ext, name=P["title"], status="stock", platform=plat,
            date=P.get("date") or today, price=float(P.get("price") or 0), photo=P.get("photo") or "",
            notes=f"Achat {plat} ajouté par l'extension" + (f" · {P['note']}" if P.get("note") else ""))
        rep["created"].append(P["title"])
    return rep
