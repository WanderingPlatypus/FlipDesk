"""Sync of listings / sales / purchases pushed by the FlipDesk browser extension.

Every item can carry several external ids (column ext_ids, space separated):
  vinted:item:<id>   a Vinted listing         lbc:ad:<list_id>   a Leboncoin listing
  vinted:tx:<id>     a Vinted sale            lbc:tx:<id>        a Leboncoin sale
  vinted:buy:<id>    a Vinted purchase        lbc:buy:<id>       a Leboncoin purchase
An external id is never imported twice, and ids of deleted items are remembered
(table ignored) so a sync never brings back something the user removed.
The sync never touches what the user typed: costs, lots, notes, names.
"""
import re
import unicodedata
from datetime import date

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


def find(c, ext):
    return c.execute("SELECT * FROM items WHERE ext_ids LIKE ?", (f"% {ext} %",)).fetchone()


def add_ext(c, iid, ext):
    row = c.execute("SELECT ext_ids FROM items WHERE id=?", (iid,)).fetchone()
    cur = set((row["ext_ids"] or "").split()) | {ext}
    c.execute("UPDATE items SET ext_ids=? WHERE id=?", (" " + " ".join(sorted(cur)) + " ", iid))


def ignored(c, ext):
    return c.execute("SELECT 1 FROM ignored WHERE ext=?", (ext,)).fetchone() is not None


def update(c, iid, now, **kw):
    if kw:
        kw["updated_at"] = now
        c.execute(f"UPDATE items SET {','.join(k + '=?' for k in kw)} WHERE id=?", list(kw.values()) + [iid])


def create(c, now, ext, **kw):
    kw.setdefault("status", "stock")
    kw["created_at"] = kw["updated_at"] = now
    kw["ext_ids"] = f" {ext} "
    cur = c.execute(f"INSERT INTO items ({','.join(kw)}) VALUES ({','.join('?' * len(kw))})", list(kw.values()))
    return cur.lastrowid


def unsold(c):
    return c.execute("SELECT * FROM items WHERE status IN ('stock','listed')").fetchall()


def run(c, payload, now):
    plat = payload.get("platform")
    if plat not in PREFIX:
        raise ValueError("Plateforme inconnue")
    pre = PREFIX[plat]
    other = [p for p in PREFIX.values() if p != pre][0]
    today = date.today().isoformat()
    rep = {"platform": plat, "created": [], "updated": [], "sold": [], "warnings": []}

    # ---------- listings ----------
    for L in payload.get("listings", []):
        ext, price = L["ext"], float(L.get("price") or 0)
        if ignored(c, ext):
            continue
        it = find(c, ext)
        if it:
            if it["status"] in ("stock", "listed"):
                ch = {}
                if price and abs(price - (it["list_price"] or 0)) > 0.001:
                    ch["list_price"] = price
                if it["status"] == "stock" and L.get("active"):
                    ch.update(status="listed", list_date=it["list_date"] or L.get("date") or today)
                if not it["url"] and L.get("url"):
                    ch["url"] = L["url"]
                if ch:
                    update(c, it["id"], now, **ch)
                    rep["updated"].append(it["name"])
            elif it["status"] == "sold" and L.get("active") and it["sell_platform"] != plat:
                rep["warnings"].append(f"« {it['name']} » est vendu sur {it['sell_platform']} mais toujours en ligne sur {plat}")
            continue
        if L.get("sold"):
            continue  # an already sold listing we never saw: the sale will create it
        # same item already known (imported, or listed on the other site)?
        cands = [r for r in unsold(c) if title_match(r["name"], L["title"])
                 and not any(e.startswith(pre + ":item:") or e.startswith(pre + ":ad:") for e in exts(r))]
        r = pick(cands, [lambda r: abs((r["list_price"] or 0) - price) < .01,
                         lambda r: close(r["list_price"] or 0, price, .15),
                         lambda r: not r["list_price"] and r["status"] == "stock"])
        if r is None:  # maybe already sold on the other site but still online here
            sold = [x for x in c.execute("SELECT * FROM items WHERE status='sold' AND sell_platform!=? AND sell_date>=date('now','-60 day')", (plat,))
                    if title_match(x["name"], L["title"]) and close(x["list_price"] or x["sell_price"] or 0, price, .15)]
            if len(sold) == 1:
                add_ext(c, sold[0]["id"], ext)
                if L.get("active"):
                    rep["warnings"].append(f"« {sold[0]['name']} » est vendu sur {sold[0]['sell_platform']} mais toujours en ligne sur {plat}")
                continue
        if r is not None:
            add_ext(c, r["id"], ext)
            ch = {}
            if not r["list_price"] and price:
                ch["list_price"] = price
            if r["status"] == "stock":
                ch.update(status="listed", list_date=L.get("date") or today)
            if not r["url"] and L.get("url"):
                ch["url"] = L["url"]
            update(c, r["id"], now, **ch)
            rep["updated"].append(r["name"])
            continue
        create(c, now, ext, name=L["title"], status="listed", list_price=price,
               list_date=L.get("date") or today, url=L.get("url") or "", condition=L.get("condition") or "",
               notes=f"Annonce {plat} ajoutée par l'extension")
        rep["created"].append(L["title"])

    # ---------- sales ----------
    for S in payload.get("sales", []):
        ext, price = S["ext"], float(S.get("price") or 0)
        if ignored(c, ext):
            continue
        it = find(c, ext)
        if S.get("cancelled"):
            if it and it["status"] == "sold" and it["sell_platform"] == plat:
                update(c, it["id"], now, status="listed", sell_platform="", sell_date="", sell_price=0)
                rep["updated"].append(it["name"] + " (vente annulée)")
            continue
        if it:
            ch = {}
            if it["status"] != "sold":
                ch.update(status="sold", sell_platform=plat, sell_date=S.get("date") or today, sell_price=price)
            elif it["sell_platform"] == plat and price and abs(price - (it["sell_price"] or 0)) > 0.001:
                ch["sell_price"] = price
            if ch:
                update(c, it["id"], now, **ch)
                rep["sold" if "status" in ch else "updated"].append(it["name"])
            continue
        target = find(c, S["item_ext"]) if S.get("item_ext") else None
        if not target:
            cands = [r for r in unsold(c) if title_match(r["name"], S["title"])]
            mine = [r for r in cands if any(e.startswith(pre + ":") for e in exts(r))] or cands
            target = pick(mine, [lambda r: abs((r["list_price"] or 0) - price) < .01,
                                 lambda r: close(r["list_price"] or 0, price, .35),
                                 lambda r: not r["list_price"]])
        if target and target["status"] != "sold":
            add_ext(c, target["id"], ext)
            update(c, target["id"], now, status="sold", sell_platform=plat,
                   sell_date=S.get("date") or today, sell_price=price)
            rep["sold"].append(target["name"])
            if any(e.startswith(other + ":") for e in exts(target)):
                rep["warnings"].append(f"« {target['name']} » vendu sur {plat} : pense à retirer l'annonce de l'autre site")
            continue
        create(c, now, ext, name=S["title"], status="sold", sell_platform=plat,
               sell_date=S.get("date") or today, sell_price=price,
               notes=f"Vente {plat} ajoutée par l'extension · prix d'achat à compléter")
        rep["sold"].append(S["title"])

    # ---------- purchases ----------
    for P in payload.get("purchases", []):
        ext = P["ext"]
        if P.get("cancelled") or ignored(c, ext) or find(c, ext):
            continue
        create(c, now, ext, name=P["title"], status="stock", buy_platform=plat,
               buy_date=P.get("date") or today, buy_price=float(P.get("price") or 0),
               notes=f"Achat {plat} ajouté par l'extension" + (f" · {P['note']}" if P.get("note") else ""))
        rep["created"].append(P["title"])
    return rep


NOTE_EXT = [  # ids written in notes by the earlier one-shot imports
    (r"Vente Leboncoin n°(\d+)", "lbc:tx:"), (r"Transaction Leboncoin (\d+)", "lbc:tx:"),
    (r"Achat Leboncoin n°(\d+)", "lbc:buy:"), (r"Vente Vinted n°(\d+)", "vinted:tx:"),
    (r"Achat Vinted n°(\d+)", "vinted:buy:"),
]


def ext_from_notes(c):
    for r in c.execute("SELECT id, notes FROM items WHERE notes != ''").fetchall():
        for rx, pre in NOTE_EXT:
            for m in re.findall(rx, r["notes"]):
                add_ext(c, r["id"], pre + m)
