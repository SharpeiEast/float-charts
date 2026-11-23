#!/usr/bin/env python3
"""Leader tracker: keeps downloading the fills of 16 Binance copy-trading lead traders into SQLite (Binance only keeps a limited window),
and serves a single page (left: action table, right: K-line chart with their entries/exits). BTCUSDT / ETHUSDT only on the page; ALL symbols are stored.

  python3 leader_tracker.py [--host 127.0.0.1 --host 100.78.175.68] [--port 8780] [--db data/leaders.sqlite]
API: GET /api/meta | /api/actions?inst=ETHUSDT&traders=id,id&from=ms&to=ms&limit=300 | /api/klines?symbol=&interval=&limit=&endTime= | /api/ping
"""
import argparse, json, os, sqlite3, threading, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))


def web_file(name):
    """page + chart lib: repo layout (../web/leader_float.html) or the deployed folder next to this script (index.html)"""
    for d, n in ((os.path.join(HERE, "..", "web"), name), (HERE, "index.html" if name == "leader_float.html" else name)):
        p = os.path.join(d, n)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(name)
BAPI = "https://www.binance.com/bapi/futures/v1/friendly/future/copy-trade/lead-portfolio/"
FAPI = "https://fapi.binance.com/fapi/v1/klines"
RAW = [("5075281354358777856", "熬鹰资本"), ("5140488422016171264", "富一次就足够"), ("5126298543040831233", "Bran Brandon"),
       ("4794150057005208320", "Btc星辰"), ("5221328836951983617", "Eth孙满仓"), ("5108371059752839168", "鎏渊"),
       ("4751838302089254401", "Btc星辰"), ("5170016259600335617", "爽翻天"), ("4708220152086930177", "风雪哥"),
       ("5289608959515124481", "鎏渊"), ("5312928647505109504", "金木爱交易"), ("5305355976370339073", "金木爱交易"),
       ("5301374102922594049", "500u起家-"), ("5075332206126952449", "等风来赢"), ("4101790650458965760", "Channel_CMT"),
       ("5285947977434857729", "金木爱交易")]
PUBLIC_POS = {"4708220152086930177", "5312928647505109504", "5301374102922594049", "5075332206126952449"}
COLORS = ["#ff9f1c", "#2ec4b6", "#e71d36", "#9b5de5", "#00bbf9", "#f15bb5", "#fee440", "#8ac926", "#ff595e", "#1982c4",
          "#6a4c93", "#ffca3a", "#4cc9f0", "#f72585", "#90be6d", "#b5838d"]
# Binance returns no fills and no position history at all for these (checked 2026-11-23), so they are not watched
EXCLUDE = {"4794150057005208320", "5289608959515124481", "5312928647505109504", "5301374102922594049", "5075332206126952449"}
names = [n for tid, n in RAW if tid not in EXCLUDE]
TRADERS = []
for i, (tid, n) in enumerate(RAW):                      # colour = original index, so it stays stable when someone is excluded
    if tid in EXCLUDE:
        continue
    nm = n + "#" + tid[-4:] if names.count(n) > 1 else n
    TRADERS.append(dict(id=tid, name=nm, short=n[:2], color=COLORS[i], public_pos=tid in PUBLIC_POS))
TID = {t["id"]: t for t in TRADERS}
SYMS = ("BTCUSDT", "ETHUSDT")
STATUS = {t["id"]: dict(ok=0, err="", cycles=0) for t in TRADERS}
LOCK = threading.RLock()
DB = None


def now_ms():
    return int(time.time() * 1000)


def log(m):
    print(time.strftime("%F %T"), m, flush=True)


def init_db(path):
    global DB
    os.makedirs(os.path.dirname(path), exist_ok=True)
    DB = sqlite3.connect(path, check_same_thread=False)
    DB.execute("PRAGMA journal_mode=WAL")
    DB.execute("CREATE TABLE IF NOT EXISTS fills(trader TEXT, time INTEGER, symbol TEXT, side TEXT, pside TEXT, price REAL, qty REAL, notional REAL, fee REAL, pnl REAL)")
    DB.execute("CREATE INDEX IF NOT EXISTS ix_fills ON fills(trader, symbol, time)")
    DB.execute("CREATE TABLE IF NOT EXISTS positions(trader TEXT, pid TEXT, symbol TEXT, side TEXT, lev INTEGER, opened INTEGER, closed INTEGER, avg_cost REAL, avg_close REAL, qty REAL, closed_vol REAL, pnl REAL, roi REAL, status TEXT, updated INTEGER, PRIMARY KEY(trader, pid))")
    DB.commit()


def bapi(ep, body, tries=4):
    err = ""
    for i in range(tries):
        try:
            r = urllib.request.Request(BAPI + ep, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "clienttype": "web", "User-Agent": "Mozilla/5.0"})
            d = json.load(urllib.request.urlopen(r, timeout=30))
            if d.get("code") == "000000":
                return d["data"]
            err = str(d.get("message") or d.get("code"))
        except Exception as e:
            err = repr(e)
        time.sleep(10 if "busy" in err.lower() else 3 * (i + 1))
    raise RuntimeError(err)


def sync_fills(tid):
    with LOCK:
        last = DB.execute("SELECT MAX(time) FROM fills WHERE trader=?", (tid,)).fetchone()[0] or 0
    fetched, page = [], 1
    while page <= 130:
        d = bapi("trade-history", {"pageNumber": page, "pageSize": 50, "portfolioId": tid})
        L = d.get("list") or []
        if not L:
            break
        fetched += L
        if L[-1]["time"] < last or len(fetched) >= int(d.get("total") or 10 ** 9):
            break
        page += 1
        time.sleep(0.25 if last == 0 else 0.4)
    new = [x for x in fetched if x["time"] > last]
    bound = [x for x in fetched if x["time"] == last]
    if bound:
        with LOCK:
            k = DB.execute("SELECT COUNT(*) FROM fills WHERE trader=? AND time=?", (tid, last)).fetchone()[0]
        if len(bound) > k:
            new += bound[:len(bound) - k]
    if new:
        rows = [(tid, x["time"], x["symbol"], x["side"], x["positionSide"], float(x["price"]), float(x["qty"]), float(x["quantity"]), float(x.get("fee") or 0), float(x.get("realizedProfit") or 0)) for x in new]
        with LOCK:
            DB.executemany("INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
            DB.commit()
    return len(new)


def sync_positions(tid, max_pages=2):
    n = 0
    for page in range(1, max_pages + 1):
        d = bapi("position-history", {"pageNumber": page, "pageSize": 50, "portfolioId": tid, "sort": "OPENING"})
        L = d.get("list") or []
        rows = [(tid, str(p["positionId"]), p["symbol"], p["side"], int(p["leverage"]), p["opened"], p.get("closed"), float(p["avgCost"]), float(p.get("avgClosePrice") or 0),
                 float(p["maxOpenInterest"]), float(p["closedVolume"]), float(p["closingPnl"]), float(p["roi"]), p["status"], now_ms()) for p in L]
        with LOCK:
            DB.executemany("INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            DB.commit()
        n += len(rows)
        if len(L) < 50:
            break
        time.sleep(0.5)
    return n


def pos_collector():
    """position-history (open/close time, avg prices, leverage, pnl, roi per position) reaches back months; first pass goes deep, later passes only refresh the newest pages"""
    while True:
        for t in TRADERS:
            try:
                with LOCK:
                    have = DB.execute("SELECT COUNT(*) FROM positions WHERE trader=?", (t["id"],)).fetchone()[0]
                n = sync_positions(t["id"], 2 if have else 60)
                if not have and n:
                    log(f"{t['name']}: +{n} positions")
            except Exception as e:
                log(f"{t['name']}: positions error {e!r}")
            time.sleep(1.5)
        time.sleep(600)


def collector():
    while True:
        for t in TRADERS:
            s = STATUS[t["id"]]
            try:
                n = sync_fills(t["id"])
                s["ok"], s["err"] = now_ms(), ""
                if n:
                    log(f"{t['name']}: +{n} fills")
            except Exception as e:
                s["err"] = repr(e)[:160]
                log(f"{t['name']}: fills error {e!r}")
            s["cycles"] += 1
            time.sleep(1.2)


# ------------------------------------------------------------------ actions (grouped fills)
GAP = 180_000


def group_actions(rows, lev_rows):
    """rows: fills of one trader+symbol ordered by time -> grouped actions"""
    out, open_g = [], {}
    for (tm, side, ps, price, qty, notional, pnl) in rows:
        if ps == "BOTH":
            opening = pnl == 0
            direction = ("long" if side == "BUY" else "short") if opening else ("long" if side == "SELL" else "short")
        else:
            opening = (ps == "LONG") == (side == "BUY")
            direction = "long" if ps == "LONG" else "short"
        key = (direction, opening)
        g = open_g.get(key)
        if g and tm - g["t1"] <= GAP:
            g["t1"] = tm; g["qty"] += qty; g["notional"] += notional; g["pnl"] += pnl; g["n"] += 1
        else:
            g = dict(dir=direction, kind="open" if opening else "close", t0=tm, t1=tm, qty=qty, notional=notional, pnl=pnl, n=1)
            open_g[key] = g; out.append(g)
    for g in out:
        g["price"] = g["notional"] / g["qty"] if g["qty"] else 0
        g["lev"] = None
        for (side, lev, opened, closed) in lev_rows:
            if side.lower() != g["dir"]:
                continue
            ref, tgt = (opened, g["t0"]) if g["kind"] == "open" else (closed, g["t1"])
            if ref and abs(ref - tgt) < 600_000:
                g["lev"] = lev; break
    return out


def get_actions(inst, ids, t0, t1, limit):
    res = []
    for tid in ids:
        if tid not in TID:
            continue
        with LOCK:
            rows = DB.execute("SELECT time,side,pside,price,qty,notional,pnl FROM fills WHERE trader=? AND symbol=? AND time BETWEEN ? AND ? ORDER BY time", (tid, inst, t0, t1)).fetchall()
            lv = DB.execute("SELECT side,lev,opened,closed FROM positions WHERE trader=? AND symbol=?", (tid, inst)).fetchall()
            cov = DB.execute("SELECT MIN(time) FROM fills WHERE trader=?", (tid,)).fetchone()[0] or now_ms() + 10 ** 9
            ps = DB.execute("SELECT side,lev,opened,closed,avg_cost,avg_close,qty,closed_vol,pnl,roi FROM positions WHERE trader=? AND symbol=?", (tid, inst)).fetchall()
        for g in group_actions(rows, lv):
            g.update(trader=tid, inst=inst, src="fill"); res.append(g)
        # periods older than the stored fills: position-level actions (open / close time only, no add/reduce steps)
        for (side, lev, opened, closed, ac, acl, q, cq, pnl, roi) in ps:
            d = side.lower()
            if opened and opened < cov and t0 <= opened <= t1:
                res.append(dict(dir=d, kind="open", t0=opened, t1=opened, qty=q, notional=q * ac, pnl=0.0, n=0, price=ac, lev=lev, trader=tid, inst=inst, src="pos"))
            if closed and closed < cov and t0 <= closed <= t1:
                res.append(dict(dir=d, kind="close", t0=closed, t1=closed, qty=cq or q, notional=(cq or q) * acl, pnl=pnl, n=0, price=acl, lev=lev, roi=roi, trader=tid, inst=inst, src="pos"))
    res.sort(key=lambda g: -g["t1"])
    return res[:limit]


def meta():
    out = []
    with LOCK:
        for t in TRADERS:
            tid = t["id"]
            n = DB.execute("SELECT COUNT(*), MAX(time), MIN(time) FROM fills WHERE trader=?", (tid,)).fetchone()
            ne = DB.execute("SELECT COUNT(*) FROM fills WHERE trader=? AND symbol IN ('BTCUSDT','ETHUSDT')", (tid,)).fetchone()[0]
            pn = DB.execute("SELECT COUNT(*), MIN(opened) FROM positions WHERE trader=?", (tid,)).fetchone()
            out.append(dict(t, pos=pn[0], first_pos=pn[1], fills=n[0], last_fill=n[1], first_fill=n[2], fills_be=ne, last_ok=STATUS[tid]["ok"], err=STATUS[tid]["err"], cycles=STATUS[tid]["cycles"]))
    return {"now": now_ms(), "traders": out}


_kc = {}
_cache = {}


def cached(key, ttl, fn):
    c = _cache.get(key)
    if c and time.time() - c[0] < ttl:
        return c[1]
    v = fn()
    _cache[key] = (time.time(), v)
    return v


def mark_prices():
    def go():
        d = json.load(urllib.request.urlopen(urllib.request.Request("https://fapi.binance.com/fapi/v1/ticker/price", headers={"User-Agent": "Mozilla/5.0"}), timeout=20))
        return {x["symbol"]: float(x["price"]) for x in d}
    return cached("mark", 3, go)


def position_show(tid):
    def go():
        r = urllib.request.Request(BAPI + "detail?portfolioId=" + tid, headers={"clienttype": "web", "User-Agent": "Mozilla/5.0"})
        return bool((json.load(urllib.request.urlopen(r, timeout=20)).get("data") or {}).get("positionShow"))
    try:
        return cached("show" + tid, 600, go)
    except Exception:
        return False


def live_positions(tid):
    def go():
        r = urllib.request.Request(BAPI.replace("lead-portfolio", "lead-data") + "positions?portfolioId=" + tid, headers={"clienttype": "web", "User-Agent": "Mozilla/5.0"})
        d = json.load(urllib.request.urlopen(r, timeout=20))
        if d.get("code") != "000000":
            raise RuntimeError(str(d.get("message")))
        return d.get("data") or []
    return cached("live" + tid, 8, go)


def derived_holdings(tid):
    """Hidden positions: walk the stored fills; a close larger than what we have seen opened is ignored (that part was opened before the first stored fill)."""
    with LOCK:
        rows = DB.execute("SELECT time,symbol,side,pside,price,qty,pnl FROM fills WHERE trader=? ORDER BY time", (tid,)).fetchall()
        lv = DB.execute("SELECT symbol,side,lev,opened FROM positions WHERE trader=? ORDER BY opened", (tid,)).fetchall()
    st = {}
    for (tm, sym, side, ps, price, qty, pnl) in rows:
        if ps == "BOTH":
            opening = pnl == 0
            d = ("long" if side == "BUY" else "short") if opening else ("long" if side == "SELL" else "short")
        else:
            opening = (ps == "LONG") == (side == "BUY")
            d = "long" if ps == "LONG" else "short"
        h = st.setdefault((sym, d), dict(qty=0.0, cost=0.0, t=None))
        if opening:
            if h["qty"] <= 1e-12:
                h["t"] = tm
            h["qty"] += qty; h["cost"] += qty * price
        elif h["qty"] > 0:
            f = min(qty, h["qty"]) / h["qty"]
            h["cost"] *= 1 - f; h["qty"] -= min(qty, h["qty"])
            if h["qty"] <= 1e-9 * max(1.0, qty):
                h["qty"] = 0.0; h["cost"] = 0.0; h["t"] = None
    out = []
    for (sym, d), h in st.items():
        if h["qty"] > 0:
            last = [l for (s2, sd, l, o) in lv if s2 == sym and sd.lower() == d]
            out.append(dict(symbol=sym, dir=d, qty=h["qty"], entry=h["cost"] / h["qty"], opened=h["t"], lev=last[-1] if last else None, lev_guess=True, src="est"))
    return out


def get_holdings(ids):
    px = {}
    try:
        px = mark_prices()
    except Exception:
        pass
    res, older, live = [], {}, {}
    for tid in ids:
        if tid not in TID:
            continue
        rows = None
        if tid in PUBLIC_POS or position_show(tid):
            try:
                rows = []
                for x in live_positions(tid):
                    amt = float(x["positionAmount"])
                    if abs(amt) < 1e-12:
                        continue
                    d = "long" if (x["positionSide"] == "LONG" or (x["positionSide"] == "BOTH" and amt > 0)) else "short"
                    rows.append(dict(symbol=x["symbol"], dir=d, qty=abs(amt), entry=float(x["entryPrice"]), mark=float(x.get("markPrice") or 0) or None,
                                     upnl=float(x["unrealizedProfit"]), lev=int(x["leverage"]), lev_guess=False, opened=None, src="live"))
                live[tid] = True
            except Exception:
                rows = None
        if rows is None:
            live[tid] = False
            rows = derived_holdings(tid)
            with LOCK:
                cov = DB.execute("SELECT MIN(time) FROM fills WHERE trader=?", (tid,)).fetchone()[0] or 0
                older[tid] = DB.execute("SELECT COUNT(*) FROM positions WHERE trader=? AND closed IS NULL AND opened<?", (tid, cov)).fetchone()[0]
        for r in rows:
            m = r.get("mark") or px.get(r["symbol"])
            r["mark"] = m
            sg = 1 if r["dir"] == "long" else -1
            if r.get("upnl") is None and m:
                r["upnl"] = (m - r["entry"]) * r["qty"] * sg
            r["notional"] = r["qty"] * (m or r["entry"])
            r["trader"] = tid
            res.append(r)
    res.sort(key=lambda r: -r["notional"])
    return {"holdings": res, "older": older, "live": live, "now": now_ms()}


def klines(q):
    sym = q.get("symbol", ["ETHUSDT"])[0]
    iv = q.get("interval", ["5m"])[0]
    if sym not in SYMS or iv not in ("1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"):
        raise ValueError("bad params")
    p = {"symbol": sym, "interval": iv, "limit": str(min(1500, int(q.get("limit", ["1000"])[0])))}
    if q.get("endTime"):
        p["endTime"] = str(int(q["endTime"][0]))
    url = FAPI + "?" + urllib.parse.urlencode(p)
    c = _kc.get(url)
    if c and time.time() - c[0] < 1.5:
        return c[1]
    d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20))
    out = [[x[0], float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in d]
    _kc[url] = (time.time(), out)
    if len(_kc) > 300:
        _kc.clear()
    return out


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")          # the desktop app loads the page from a local file
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                return self.send(200, open(web_file("leader_float.html"), "rb").read(), "text/html; charset=utf-8")
            if u.path == "/lwc.js":
                return self.send(200, open(web_file("lwc.js"), "rb").read(), "application/javascript")
            if u.path == "/api/ping":
                return self.send(200, {"ok": True, "now": now_ms()})
            if u.path == "/api/meta":
                return self.send(200, meta())
            if u.path == "/api/klines":
                return self.send(200, klines(q))
            if u.path == "/api/holdings":
                ids = [x for x in (q.get("traders", [""])[0]).split(",") if x] or [t["id"] for t in TRADERS]
                return self.send(200, get_holdings(ids))
            if u.path == "/api/actions":
                inst = q.get("inst", ["ETHUSDT"])[0]
                if inst not in SYMS:
                    return self.send(400, {"error": "bad inst"})
                ids = [x for x in (q.get("traders", [""])[0]).split(",") if x] or [t["id"] for t in TRADERS]
                t0, t1 = int(q.get("from", ["0"])[0]), int(q.get("to", [str(now_ms() + 10 ** 9)])[0])
                return self.send(200, {"actions": get_actions(inst, ids, t0, t1, min(int(q.get("limit", ["300"])[0]), 20000))})
            self.send(404, {"error": "not found"})
        except Exception as e:
            self.send(500, {"error": repr(e)[:200]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", action="append")
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--db", default=os.path.join(HERE, "data", "leaders.sqlite"))
    a = ap.parse_args()
    init_db(a.db)
    threading.Thread(target=collector, daemon=True).start()
    threading.Thread(target=pos_collector, daemon=True).start()
    for host in (a.host or ["127.0.0.1"])[1:]:
        threading.Thread(target=lambda h=host: ThreadingHTTPServer((h, a.port), H).serve_forever(), daemon=True).start()
    log("started")
    ThreadingHTTPServer(((a.host or ["127.0.0.1"])[0], a.port), H).serve_forever()


if __name__ == "__main__":
    main()
