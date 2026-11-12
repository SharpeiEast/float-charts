#!/usr/bin/env python3
"""SIMU server: keeps the simulated contract orders / positions of qqqusdt_float running on an always-on machine.

It watches the Binance USDT-M futures price, fills limit orders, cancels them when the countdown ends, closes positions on
take-profit / stop-loss / liquidation, stores everything in a JSON file and (optionally) sends a Bark message on a fill or an
automatic close. The page (web/qqqusdt_float.html) is served from here too and talks to the JSON API below.

Python standard library only.

  python3 simu_server.py [--host 100.78.175.68 --host 127.0.0.1] [--port 8765] [--state ~/simu/state.json] [--html path]

API (JSON, header X-Token or ?tok= must match the token file, except /api/ping):
  GET  /api/state?inst=btc          full state + prices (also marks that instrument as watched for a minute)
  POST /api/order        {inst, side, lev, tp, sl, fee, mode: "mkt"|"lmt", price, mins}
  POST /api/order/edit   {price?, lev?, tp?, sl?, mins?}
  POST /api/order/cancel
  POST /api/pos/edit     {tp?, sl?}
  POST /api/pos/close
  POST /api/ack          drop the oldest trade summary (the page has shown it)
"""
import argparse, hmac, ipaddress, json, os, secrets, subprocess, sys, threading, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SYM = {"qqq": "QQQUSDT", "eth": "ETHUSDT", "btc": "BTCUSDT"}
TR = {"qqq": {"max": 25, "mmr": 0.02}, "eth": {"max": 150, "mmr": 0.004}, "btc": {"max": 150, "mmr": 0.004}}
LABEL = {"qqq": "QQQ", "eth": "ETH", "btc": "BTC"}
TAKER, MAKER, DEF_EXP = 0.0005, 0.0002, 30
FAPI = "https://fapi.binance.com"
TEST = bool(os.environ.get("SIMU_TEST"))
TAILNET = ipaddress.ip_network("100.64.0.0/10")


def now_ms():
    return int(time.time() * 1000)


# ---------------------------------------------------------------- network layer (replaced by a fake one in tests)
class Net:
    def get(self, path, **params):
        url = FAPI + path + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "simu"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode())

    def price(self, sym):
        return float(self.get("/fapi/v1/ticker/price", symbol=sym)["price"])

    def latest(self, sym):                      # (price, aggTradeId, time) of the newest trade
        a = self.get("/fapi/v1/aggTrades", symbol=sym, limit=1)
        if not a:
            return self.price(sym), None, now_ms()
        return float(a[-1]["p"]), int(a[-1]["a"]), int(a[-1]["T"])

    def agg_from(self, sym, from_id):           # trades with id >= from_id, oldest first
        return [(int(t["a"]), int(t["T"]), float(t["p"])) for t in self.get("/fapi/v1/aggTrades", symbol=sym, fromId=from_id, limit=1000)]

    def klines(self, sym, start_ms):            # 1m candles opening at or after start_ms
        return [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4])) for k in
                self.get("/fapi/v1/klines", symbol=sym, interval="1m", startTime=start_ms, limit=1500)]


class FakeNet:
    """Test double: ticks are pushed in through /api/_test/tick."""
    def __init__(self):
        self.trades = {}       # sym -> list of (id, T, p)
        self.px = {}

    def push(self, sym, p, t=None):
        L = self.trades.setdefault(sym, [])
        L.append((len(L) + 1, t or now_ms(), float(p)))
        self.px[sym] = float(p)

    def price(self, sym):
        return self.px.get(sym, 100.0)

    def latest(self, sym):
        L = self.trades.get(sym)
        if not L:
            return self.price(sym), None, now_ms()
        return L[-1][2], L[-1][0], L[-1][1]

    def agg_from(self, sym, from_id):
        return [t for t in self.trades.get(sym, []) if t[0] >= from_id][:1000]

    def klines(self, sym, start_ms):
        return []


# ---------------------------------------------------------------- contract maths (same as the page)
def kind_of(side, lim, cur):
    return "now" if lim == cur else ("limit" if (side > 0) == (lim < cur) else "stop")


def fo_of(q):
    return q["fo"] if q.get("fo") is not None else TAKER


def roe_at(p, q):
    r = p / q["entry"]
    v = q["side"] * q["lev"] * (r - 1)
    if q["fee"]:
        v -= q["lev"] * (fo_of(q) + TAKER * r)
    return v


def liq_of(q):
    m = TR[q["inst"]]["mmr"]
    f0 = q["lev"] * fo_of(q) if q["fee"] else 0
    return q["entry"] * (q["side"] * q["lev"] - 1 + f0) / (q["lev"] * (q["side"] - m))


def fnum(x):
    try:
        v = float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def fmt(v):
    return f"{v:,.1f}" if v >= 1000 else f"{v:,.2f}"


class Bad(Exception):
    pass


# ---------------------------------------------------------------- the engine
class Engine:
    def __init__(self, path, net, notify):
        self.path, self.net, self.notify = path, net, notify
        self.lock = threading.RLock()
        self.prices = {}          # sym -> [price, time_ms]
        self.watch = {}           # sym -> watched until (ms)
        self.cur = {}             # sym -> {"id": last aggTrade id seen, "ts": its time}
        self.st = {"rev": 0, "seq": 0, "ord": None, "pos": None, "sums": [], "cur": {}}
        self.saved_at = 0
        try:
            with open(path) as f:
                self.st.update(json.load(f))
            self.cur = {k: dict(v) for k, v in self.st.get("cur", {}).items()}
        except (OSError, ValueError):
            pass

    # -- persistence
    def save(self, force=False):
        with self.lock:
            self.st["cur"] = self.cur
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.st, f)
            os.replace(tmp, self.path)
            self.saved_at = time.time()

    def bump(self):
        self.st["rev"] += 1
        self.save()

    def view(self, inst=None):
        with self.lock:
            if inst in SYM:
                self.watch[SYM[inst]] = now_ms() + 60000
            return {"now": now_ms(), "rev": self.st["rev"], "ord": self.st["ord"], "pos": self.st["pos"], "sums": self.st["sums"],
                    "prices": {s: {"p": v[0], "t": v[1]} for s, v in self.prices.items()}, "tr": TR}

    # -- price handling
    def set_price(self, sym, p, t):
        self.prices[sym] = [p, t]

    def fresh_price(self, sym):
        """latest known price, refreshed over REST when older than 4 s"""
        v = self.prices.get(sym)
        if v is None or now_ms() - v[1] > 4000:
            p = self.net.price(sym)
            self.set_price(sym, p, now_ms())
            return p
        return v[0]

    # -- orders
    def place(self, b):
        inst = b.get("inst")
        if inst not in SYM:
            raise Bad("unknown instrument")
        side = 1 if int(b.get("side", 1)) > 0 else -1
        lev = max(1, min(TR[inst]["max"], int(round(float(b.get("lev", 1))))))
        tp, sl = fnum(b.get("tp")), fnum(b.get("sl"))
        fee = bool(b.get("fee", True))
        sym = SYM[inst]
        cur_id = self.net.latest(sym)
        with self.lock:
            if self.st["pos"] or self.st["ord"]:
                raise Bad("already have an open position or pending order")
        cur = self.net.price(sym)
        self.set_price(sym, cur, now_ms())
        base = {"inst": inst, "side": side, "lev": lev, "tp": tp, "sl": sl, "fee": fee}
        mode = b.get("mode", "mkt")
        ref = cur
        if mode == "lmt":
            lp = fnum(b.get("price"))
            if not lp:
                raise Bad("limit price needed")
            ref = lp
        if tp and (tp <= ref if side > 0 else tp >= ref):
            raise Bad("take-profit price not sensible")
        if sl and (sl >= ref if side > 0 else sl <= ref):
            raise Bad("stop-loss price not sensible")
        with self.lock:
            self.cur[sym] = {"id": cur_id[1], "ts": cur_id[2]} if cur_id[1] is not None else {"id": None, "ts": now_ms()}
            self.st["seq"] += 1
            if mode == "lmt":
                mins = DEF_EXP
                if b.get("mins") not in (None, ""):
                    mins = float(b["mins"])
                    if not 1 <= mins <= 1440:
                        raise Bad("countdown must be 1 to 1440 minutes")
                t = now_ms()
                self.st["ord"] = dict(base, id=self.st["seq"], price=lp, kind=kind_of(side, lp, cur), cur0=cur, mins=mins, t0=t, expAt=int(t + mins * 60000))
                self.bump()
                self.check_order(cur, t)           # a limit at the market fills at once
            else:
                self.start_pos(base, cur, TAKER, "市价", now_ms(), notify=False)
        return self.view()

    def edit_order(self, b):
        with self.lock:
            o = self.st["ord"]
            if not o:
                raise Bad("no pending order")
            sym = SYM[o["inst"]]
            n = dict(o)
            if "price" in b:
                v = fnum(b["price"])
                if not v:
                    raise Bad("limit price invalid")
                if n["tp"] and (n["tp"] <= v if n["side"] > 0 else n["tp"] >= v):
                    raise Bad("新限价与止盈价冲突, 请先改止盈价")
                if n["sl"] and (n["sl"] >= v if n["side"] > 0 else n["sl"] <= v):
                    raise Bad("新限价与止损价冲突, 请先改止损价")
                n["price"] = v
            if "lev" in b:
                v = int(round(float(b["lev"])))
                if v < 1:
                    raise Bad("杠杆至少 1 倍")
                n["lev"] = min(TR[o["inst"]]["max"], v)
            for w in ("tp", "sl"):
                if w in b:
                    if b[w] in (None, ""):
                        n[w] = None
                    else:
                        v = fnum(b[w])
                        ok = v and ((n["side"] > 0 and (v > n["price"] if w == "tp" else v < n["price"])) or
                                    (n["side"] < 0 and (v < n["price"] if w == "tp" else v > n["price"])))
                        if not ok:
                            raise Bad(("止盈" if w == "tp" else "止损") + "价需在限价" + ("上方" if (w == "tp") == (n["side"] > 0) else "下方"))
                        n[w] = v
            if "mins" in b:
                m = float(b["mins"])
                if not 1 <= m <= 1440:
                    raise Bad("倒计时需在 1 到 1440 分钟之间")
                n["mins"] = m
                n["expAt"] = int(now_ms() + m * 60000)
        cur = self.fresh_price(sym)
        with self.lock:
            if self.st["ord"] is None or self.st["ord"]["id"] != o["id"]:
                raise Bad("order changed")
            n["kind"] = kind_of(n["side"], n["price"], cur)
            self.st["ord"] = n
            self.bump()
            self.check_order(cur, now_ms())
        return self.view()

    def cancel_order(self):
        with self.lock:
            if not self.st["ord"]:
                raise Bad("no pending order")
            self.st["ord"] = None
            self.bump()
        return self.view()

    def edit_pos(self, b):
        with self.lock:
            q = self.st["pos"]
            if not q:
                raise Bad("no open position")
            sym = SYM[q["inst"]]
        p = self.fresh_price(sym)
        with self.lock:
            q = self.st["pos"]
            if not q:
                raise Bad("no open position")
            for w in ("tp", "sl"):
                if w in b:
                    if b[w] in (None, ""):
                        q[w] = None
                        continue
                    v = fnum(b[w])
                    ok = v and ((q["side"] > 0 and (v > p if w == "tp" else v < p)) or (q["side"] < 0 and (v < p if w == "tp" else v > p)))
                    if not ok:
                        raise Bad(("止盈" if w == "tp" else "止损") + "价需在现价" + ("上方" if (w == "tp") == (q["side"] > 0) else "下方"))
                    q[w] = v
            self.bump()
        return self.view()

    def close_manual(self):
        with self.lock:
            q = self.st["pos"]
            if not q:
                raise Bad("no open position")
            sym = SYM[q["inst"]]
        p = self.fresh_price(sym)
        with self.lock:
            if self.st["pos"]:
                self.close(p, "手动平仓", now_ms(), notify=False)
        return self.view()

    def ack(self):
        with self.lock:
            if self.st["sums"]:
                self.st["sums"].pop(0)
                self.bump()
        return self.view()

    # -- state transitions
    def start_pos(self, o, entry, fo, how, t, notify=True):
        q = {k: o[k] for k in ("inst", "side", "lev", "tp", "sl", "fee")}
        q.update(entry=entry, fo=fo, how=how, t0=t, hi=0.0, lo=0.0, last=entry, id=self.st["seq"])
        q["hi"] = q["lo"] = roe_at(entry, q)
        q["liq"] = liq_of(q)
        self.st["ord"] = None
        self.st["pos"] = q
        self.bump()
        if notify:                             # a market order the user placed by hand needs no message, a resting limit order that fills does
            self.say(q, f"filled at {fmt(entry)}", f"{fmt(entry)} で約定しました")

    def check_order(self, p, t):
        o = self.st["ord"]
        if not o:
            return
        if t >= o["expAt"]:
            self.st["ord"] = None
            self.bump()
            return
        k = o["kind"]
        hit = k == "now" or ((p <= o["price"] if k == "limit" else p >= o["price"]) if o["side"] > 0
                             else (p >= o["price"] if k == "limit" else p <= o["price"]))
        if not hit:
            return
        maker = k == "limit"
        self.start_pos(o, o["price"] if maker else p, MAKER if maker else TAKER,
                       "限价成交" if maker else ("现价成交" if k == "now" else "突破成交"), t)

    def close(self, px, why, t, notify=True):
        q = self.st["pos"]
        self.st["pos"] = None
        liq = why == "爆仓"
        roe = -1.0 if liq else roe_at(px, q)
        s = {"why": why, "inst": q["inst"], "side": q["side"], "lev": q["lev"], "how": q["how"], "entry": q["entry"], "px": px, "fee": q["fee"],
             "roe": roe, "move": q["side"] * (px / q["entry"] - 1), "liq": liq,
             "fees": 0.0 if (liq or not q["fee"]) else -q["lev"] * (fo_of(q) + TAKER * px / q["entry"]),
             "dur": t - q["t0"], "hi": max(q["hi"], roe), "lo": min(q["lo"], roe), "at": t}
        self.st["sums"].append(s)
        self.bump()
        if notify:
            word = {"爆仓": ("was liquidated", "は強制ロスカットとなりました"), "触发止盈": ("hit take-profit", "は利確に到達しました"),
                    "触发止损": ("hit stop-loss", "は損切りに到達しました")}.get(why, ("was closed", "は決済されました"))
            self.say(q, f"{word[0]} at {fmt(px)} ({roe * 100:+.1f}% of margin)", f"{fmt(px)} で{word[1]}(証拠金比 {roe * 100:+.1f}%)")

    def say(self, q, en, ja):
        try:
            self._say(q, en, ja)
        except Exception as e:                 # a notification problem must never break the trading state
            log(f"notify failed: {e!r}")

    def _say(self, q, en, ja):
        side = "Buy" if q["side"] > 0 else "Put"
        sj = "買い" if q["side"] > 0 else "売り"
        name = LABEL[q["inst"]]
        self.notify(f"Kota Nagasaki: Zhou San, your simulated {name} {side} {q['lev']}x position {en}. / "
                    f"周さん、模擬取引の {name} {sj} {q['lev']}倍のポジションが{ja}。")

    def on_tick(self, sym, p, t):
        """one trade price, in time order"""
        with self.lock:
            self.set_price(sym, p, t)
            o, q = self.st["ord"], self.st["pos"]
            if o and SYM[o["inst"]] == sym:
                self.check_order(p, t)
            q = self.st["pos"]
            if q and SYM[q["inst"]] == sym:
                roe = roe_at(p, q)
                changed = False
                if roe > q["hi"]:
                    q["hi"] = roe
                if roe < q["lo"]:
                    q["lo"] = roe
                q["last"] = p
                if (p <= q["liq"]) if q["side"] > 0 else (p >= q["liq"]):
                    self.close(p, "爆仓", t)
                elif q["tp"] is not None and ((p >= q["tp"]) if q["side"] > 0 else (p <= q["tp"])):
                    self.close(p, "触发止盈", t)
                elif q["sl"] is not None and ((p <= q["sl"]) if q["side"] > 0 else (p >= q["sl"])):
                    self.close(p, "触发止损", t)

    # -- polling
    def poll_agg(self, sym):
        c = self.cur.get(sym)
        if c is None or c.get("id") is None:
            p, i, ts = self.net.latest(sym)
            self.cur[sym] = {"id": i, "ts": ts}
            self.on_tick(sym, p, ts)
            return
        gap = now_ms() - c["ts"]
        if gap > 10 * 60000:
            self.catch_up_klines(sym, c)
            return
        for _ in range(6):
            tr = self.net.agg_from(sym, c["id"] + 1)
            for i, t, p in tr:
                c["id"], c["ts"] = i, t
                self.on_tick(sym, p, t)
            if len(tr) < 1000:
                break
        else:
            return
        # nothing new -> keep ts fresh so the gap logic only fires after a real outage
        c["ts"] = max(c["ts"], now_ms() - 1000) if not tr else c["ts"]

    def catch_up_klines(self, sym, c):
        """after a long outage: replay the missed 1m candles (open, then low/high in the likelier order, close)"""
        for ot, o, h, l, cl in self.net.klines(sym, c["ts"] - c["ts"] % 60000):
            if ot + 60000 <= c["ts"]:
                continue
            seq = [o, l, h, cl] if cl >= o else [o, h, l, cl]
            for n, p in enumerate(seq):
                self.on_tick(sym, p, ot + 15000 * n)
        p, i, ts = self.net.latest(sym)
        self.cur[sym] = {"id": i, "ts": ts if i is not None else now_ms()}
        self.on_tick(sym, p, max(ts, now_ms() - 1))

    def step(self):
        t = now_ms()
        with self.lock:
            o, q = self.st["ord"], self.st["pos"]
            if o and t >= o["expAt"]:
                self.st["ord"] = None
                self.bump()
            active = {SYM[x["inst"]] for x in (self.st["ord"], self.st["pos"]) if x}
            watch = {s for s, until in self.watch.items() if until > t} - active
        for sym in active:
            try:
                self.poll_agg(sym)
            except Exception as e:
                log(f"poll {sym}: {e!r}")
        for sym in watch:
            v = self.prices.get(sym)
            if v is None or t - v[1] >= 2500:
                try:
                    self.set_price(sym, self.net.price(sym), t)
                except Exception as e:
                    log(f"price {sym}: {e!r}")
        if time.time() - self.saved_at > 20:
            self.save()

    def loop(self):
        while True:
            try:
                self.step()
            except Exception as e:
                log(f"step: {e!r}")
            time.sleep(2.0 if not TEST else 0.2)


def log(msg):
    try:
        print(time.strftime("%F %T"), msg, flush=True)
    except Exception:
        pass


def bark(text):
    def run():
        try:
            subprocess.run([os.path.expanduser("~/bin/bark-notify"), text], timeout=40, capture_output=True)
        except Exception as e:
            log(f"bark failed: {e!r}")
    log("notify: " + text)
    if not os.environ.get("SIMU_NO_BARK"):
        threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------- HTTP
def make_handler(eng, token, html_path):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Token")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Private-Network", "true")

        def send(self, code, obj, ctype="application/json"):
            body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.cors()
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send(204, b"", "text/plain")

        def authed(self, q):
            if not TEST:                       # we only listen on the Tailscale address and loopback: those peers are the owner's own devices
                try:
                    ip = ipaddress.ip_address(self.client_address[0])
                    if ip.is_loopback or ip in TAILNET:
                        return True
                except ValueError:
                    pass
            t = self.headers.get("X-Token") or (q.get("tok") or [""])[0]
            return hmac.compare_digest(t.encode(), token.encode())

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path in ("/", "/index.html"):
                try:
                    with open(html_path, "rb") as f:
                        page = f.read()
                except OSError:
                    return self.send(404, {"error": "page missing"})
                page = page.replace(b"</head>", b'<meta name="simu-served" content="1"></head>', 1)
                return self.send(200, page, "text/html; charset=utf-8")
            if u.path == "/api/ping":
                return self.send(200, {"ok": True, "now": now_ms()})
            if not self.authed(q):
                return self.send(401, {"error": "bad token"})
            if u.path == "/api/state":
                return self.send(200, eng.view((q.get("inst") or [None])[0]))
            self.send(404, {"error": "not found"})

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if not self.authed(q):
                return self.send(401, {"error": "bad token"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                b = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self.send(400, {"error": "bad json"})
            try:
                r = {"/api/order": lambda: eng.place(b), "/api/order/edit": lambda: eng.edit_order(b),
                     "/api/order/cancel": eng.cancel_order, "/api/pos/edit": lambda: eng.edit_pos(b),
                     "/api/pos/close": eng.close_manual, "/api/ack": eng.ack}.get(u.path)
                if TEST and u.path == "/api/_test/tick":
                    eng.net.push(SYM[b["inst"]], b["p"], b.get("t"))
                    return self.send(200, {"ok": True})
                if r is None:
                    return self.send(404, {"error": "not found"})
                return self.send(200, r())
            except Bad as e:
                return self.send(400, {"error": str(e)})
            except Exception as e:
                log(f"{u.path}: {e!r}")
                return self.send(500, {"error": "server error: " + repr(e)})

    return H


def serve(host, port, handler):
    while True:
        try:
            srv = ThreadingHTTPServer((host, port), handler)
            srv.daemon_threads = True
            log(f"listening on {host}:{port}")
            srv.serve_forever()
        except OSError as e:
            log(f"bind {host}:{port} failed ({e}); retrying")
            time.sleep(10)


def main():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", action="append")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--state", default=os.path.expanduser("~/simu/state.json"))
    ap.add_argument("--token-file", default=os.path.expanduser("~/.config/simu/token"))
    ap.add_argument("--html", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "qqqusdt_float.html"))
    a = ap.parse_args()
    os.makedirs(os.path.dirname(a.state), exist_ok=True)
    if not os.path.exists(a.token_file):
        os.makedirs(os.path.dirname(a.token_file), exist_ok=True)
        with open(os.open(a.token_file, os.O_WRONLY | os.O_CREAT, 0o600), "w") as f:
            f.write(secrets.token_urlsafe(18))
    token = open(a.token_file).read().strip()
    eng = Engine(a.state, FakeNet() if TEST else Net(), bark)
    h = make_handler(eng, token, a.html)
    for host in a.host or ["127.0.0.1"]:
        threading.Thread(target=serve, args=(host, a.port, h), daemon=True).start()
    log("started, state " + a.state)
    eng.loop()


if __name__ == "__main__":
    main()
