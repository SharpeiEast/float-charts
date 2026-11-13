"""Desktop wrapper for the float page: web/qqqusdt_float.html shown in native always-on-top windows.

Up to MAX_WINS windows, each showing one instrument chosen from the page's drop-down (QQQUSDT, ETH perpetual, BTC
perpetual, all on Binance). A new window opens right below the lowest existing one; every window can be closed on
its own. The page talks to Binance and Yahoo directly from this computer (no server of ours in between).

Run:        python app/main.py            (add --spot for Binance spot symbols instead of the perpetuals)
Self-test:  python app/main.py --selftest result.json   (opens windows, waits, writes what the pages show, exits)
"""
import json
import os
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request

import webview

MAX_WINS = 3
DEFAULT_INSTS = ["qqq", "eth", "btc"]
DEFAULT_TF = "1h"
DEFAULT_SRV = "http://100.78.175.68:8765"      # SIMU server (Tailscale address); change it in the panel (gear button)
CFG_VER = 2           # version 2: the default period is 1h (older saved periods are reset once)
DEFAULT = {"width": 560, "height": 360, "x": 80, "y": 40, "slots": [{"inst": "qqq", "tf": DEFAULT_TF}], "srv": DEFAULT_SRV, "tok": ""}   # srv / tok: SIMU server address (pre-filled) and token; srv "" = local mode
GAP = 30 if sys.platform == "darwin" else 4          # title-bar allowance between stacked windows
TITLES = {"qqq": "QQQUSDT", "eth": "ETH 永续", "btc": "BTC 永续"}


def resource(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, rel)


def config_path():
    if sys.platform == "win32":
        root = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        root = os.path.expanduser("~/Library/Application Support")
    else:
        root = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    d = os.path.join(root, "qqqusdt-float")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "windows.json")


def log(msg):
    """Append a line to app.log next to the config file (kept small); failures here must never break the app."""
    try:
        path = os.path.join(os.path.dirname(config_path()), "app.log")
        if os.path.exists(path) and os.path.getsize(path) > 200000:
            os.remove(path)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def load_cfg():
    try:
        with open(config_path(), encoding="utf-8") as fh:
            raw = json.load(fh)
        cfg = {**DEFAULT, **raw}
        if raw.get("ver", 1) < CFG_VER:                  # config written by an older version: start from the new default period once
            for s in cfg.get("slots", []):
                if isinstance(s, dict):
                    s["tf"] = DEFAULT_TF
        cfg["ver"] = CFG_VER
        cfg["slots"] = [s for s in cfg.get("slots", []) if isinstance(s, dict) and s.get("inst") in TITLES][:MAX_WINS] or DEFAULT["slots"]
        return cfg
    except Exception:
        return json.loads(json.dumps(DEFAULT))


class SlotApi:
    """What the page of one window may call (public methods only)."""

    def __init__(self, app, slot):
        self._app = app
        self._slot = slot

    def new_window(self):
        try:
            return self._app.add_window()
        except Exception as exc:
            log("new_window failed:\n" + traceback.format_exc())
            return {"ok": False, "msg": "打开新窗口失败: %r (详情见 app.log)" % (exc,)}

    def set_state(self, inst, tf, title):
        self._app.set_state(self._slot, inst, tf, title)
        return True

    def set_server(self, url, tok):
        """The page saved a SIMU server address / token: remember them for the next start and for new windows."""
        try:
            self._app.cfg["srv"], self._app.cfg["tok"] = str(url or "").strip(), str(tok or "").strip()
            self._app._write()
            return True
        except Exception:
            log("set_server failed:\n" + traceback.format_exc())
            return False

    def set_title(self, text):
        """The page shows the rotating position summary in the window title (a minimized window still shows it in the task bar)."""
        try:
            win = self._app.wins.get(self._slot)
            if win is not None and text:
                win.set_title(str(text))
            return True
        except Exception:
            return False

    def set_grid(self, on):
        """The page switched to / from the six-period view: enlarge the window for it and restore the old size afterwards."""
        try:
            return self._app.set_grid(self._slot, bool(on))
        except Exception:
            log("set_grid failed:\n" + traceback.format_exc())
            return False

    def set_panel(self, opened, width):
        """The SIMU side panel opened / closed: widen the window to the right by the panel width (and shrink it back), so the chart keeps its size."""
        try:
            return self._app.set_panel(self._slot, bool(opened), int(width))
        except Exception:
            log("set_panel failed:\n" + traceback.format_exc())
            return False


class App:
    def __init__(self, selftest=False, spot=False):
        self.selftest = selftest
        self.spot = spot
        self.cfg = load_cfg()
        self.lock = threading.RLock()
        self.wins = {}        # slot -> window
        self.state = {}       # slot -> {"inst", "tf"}
        self.grid = {}        # slot -> [w, h] of the window before it was enlarged for the six-period view
        self.panel = {}       # slot -> width of the SIMU panel currently added to the window
        self.geo = {}         # slot -> [x, y, width, height], kept up to date from the moved/resized events (never read the window's properties from event handlers or other threads)
        self.html = resource(os.path.join("web", "qqqusdt_float.html"))

    # ---- window creation ----
    def _create(self, slot, inst, tf, x, y):
        url = self.html + "?inst=" + str(inst) + "&tf=" + str(tf) + ("&market=spot" if self.spot else "")
        if self.cfg.get("srv"):
            url += "&srv=" + urllib.parse.quote(str(self.cfg["srv"]), safe="") + "&tok=" + urllib.parse.quote(str(self.cfg.get("tok", "")), safe="")
        else:
            url += "&local"
        win = webview.create_window(TITLES.get(inst, "QQQUSDT"), url=url, js_api=SlotApi(self, slot),
                                    width=int(self.cfg["width"]), height=int(self.cfg["height"]),
                                    x=int(x), y=int(y), min_size=(360, 240), on_top=True)
        self.wins[slot] = win
        self.state[slot] = {"inst": str(inst), "tf": str(tf)}
        self.geo[slot] = [int(x), int(y), int(self.cfg["width"]), int(self.cfg["height"])]
        win.events.closed += lambda: self._closed(slot)
        win.events.moved += lambda mx, my: self._geo(slot, 0, mx, my)
        win.events.resized += lambda mw, mh: self._geo(slot, 2, mw, mh)
        win.events.closing += lambda: self._save_geometry(slot)
        # the page offers its own "浮窗" button for browsers; an app window is already on top
        win.events.loaded += lambda: self._hide_pip(win)
        return win

    @staticmethod
    def _hide_pip(win):
        try:
            win.evaluate_js("var b=document.getElementById('pip'); if (b) b.style.display='none';")
        except Exception:
            pass

    def open_initial(self):
        slots = self.cfg["slots"][:MAX_WINS]
        if self.selftest:
            slots = [{"inst": "qqq", "tf": "15m"}]  # self-test keeps its own fixed period
        for i, s in enumerate(slots):
            y = int(self.cfg["y"]) + i * (int(self.cfg["height"]) + GAP)
            self._create(i, s["inst"], s.get("tf", DEFAULT_TF), self.cfg["x"], y)

    def add_window(self, inst=None, tf=DEFAULT_TF):
        with self.lock:
            free = [s for s in range(MAX_WINS) if s not in self.wins]
            if not free:
                return {"ok": False, "msg": "最多同时打开 %d 个窗口, 请先关闭一个" % MAX_WINS}
            slot = free[0]
            used = {s["inst"] for s in self.state.values()}
            if not inst:                                  # first instrument not shown yet, in the menu's order
                inst = next((c for c in DEFAULT_INSTS if c not in used), DEFAULT_INSTS[0])
            pitch = int(self.cfg["height"]) + GAP
            above = [s for s in self.geo if s < slot]
            below = [s for s in self.geo if s > slot]
            if above:                                   # directly under the nearest window above it (fills a gap left by a closed window)
                gx, gy, gw, gh = self.geo[max(above)]
                x, y = gx, gy + gh + GAP
            elif below:                                 # slot 0 was closed: take its place above the others
                gx, gy, gw, gh = self.geo[min(below)]
                x, y = gx, max(0, gy - pitch)
            else:
                x, y = self.cfg["x"], self.cfg["y"]
            log("add_window slot=%s inst=%s at (%s, %s)" % (slot, inst, x, y))
            self._create(slot, inst, tf, x, y)
            self._save_slots()
            return {"ok": True, "slot": slot}

    # ---- bookkeeping ----
    def set_state(self, slot, inst, tf, title):
        with self.lock:
            if slot in self.state:
                self.state[slot] = {"inst": inst, "tf": tf}
            win = self.wins.get(slot)
            self._save_slots()
        if win is not None and title:
            try:
                win.set_title(title)
            except Exception:
                pass

    def set_panel(self, slot, opened, width):
        with self.lock:
            win, g = self.wins.get(slot), self.geo.get(slot)
            if win is None or g is None:
                return False
            if opened == (slot in self.panel):
                return True
            x, y, w, h = g
            if opened:
                self.panel[slot] = width
                nw = w + width
                try:                                      # keep the widened window on the screen: slide it left if it would run off the right edge
                    scr = [sc for sc in webview.screens if sc.x <= x < sc.x + sc.width] or list(webview.screens)[:1]
                    if scr and x + nw > scr[0].x + scr[0].width:
                        nx = max(scr[0].x, scr[0].x + scr[0].width - nw)
                        win.move(nx, y)
                        g[0] = nx
                except Exception:
                    pass
            else:
                width = self.panel.pop(slot)
                nw = max(360, w - width)
            win.resize(nw, h)
            g[2] = nw
            log("panel %s slot=%s width %s -> %s" % ("open" if opened else "close", slot, w, nw))
            return True

    def set_grid(self, slot, on):
        with self.lock:
            win, g = self.wins.get(slot), self.geo.get(slot)
            if win is None or g is None:
                return False
            if on == (slot in self.grid):
                return True
            x, y, w, h = g
            pw = self.panel.get(slot, 0)                      # the SIMU panel may be open: its width stays on top of the chart width
            if on:
                self.grid[slot] = [w - pw, h]
                nw, nh = max(w - pw, 1500), max(h, 860)
                try:
                    scr = [sc for sc in webview.screens if sc.x <= x < sc.x + sc.width] or list(webview.screens)[:1]
                    if scr:
                        nw, nh = min(nw, scr[0].width - 40 - pw), min(nh, scr[0].height - 80)
                        nx, ny = max(scr[0].x, min(x, scr[0].x + scr[0].width - nw - pw)), max(scr[0].y, min(y, scr[0].y + scr[0].height - nh - 40))
                        win.move(nx, ny)
                        g[0], g[1] = nx, ny
                except Exception:
                    pass
                win.resize(nw + pw, nh)
                g[2], g[3] = nw + pw, nh
            else:
                w0, h0 = self.grid.pop(slot)
                win.resize(w0 + pw, h0)
                g[2], g[3] = w0 + pw, h0
            log("grid %s slot=%s" % ("on" if on else "off", slot))
            return True

    def _geo(self, slot, at, a, b):
        try:
            g = self.geo.get(slot)
            if g is not None:
                g[at], g[at + 1] = int(a), int(b)
        except Exception:
            pass

    def _closed(self, slot):
        with self.lock:
            self.wins.pop(slot, None)
            self.state.pop(slot, None)       # keep the closed window's instrument out of the saved list
            self.geo.pop(slot, None)
            self.panel.pop(slot, None)
            self.grid.pop(slot, None)
            self._save_slots()
        log("closed slot=%s, open now: %s" % (slot, sorted(self.wins)))

    def _save_geometry(self, slot):
        try:
            g = self.geo.get(slot)
            if g and slot in self.grid:
                self.cfg["width"], self.cfg["height"] = self.grid[slot]                              # closed while in the six-period view: keep the normal size
            elif g:
                self.cfg["width"], self.cfg["height"] = g[2] - self.panel.get(slot, 0), g[3]      # without the SIMU panel
            if self.geo:
                top = min(self.geo.values(), key=lambda v: v[1])
                self.cfg["x"], self.cfg["y"] = top[0], top[1]
        except Exception:
            log("save geometry failed:\n" + traceback.format_exc())
        self._write()

    def _save_slots(self):
        self.cfg["slots"] = [self.state[s] for s in sorted(self.state)][:MAX_WINS]
        self._write()

    def _write(self):
        if self.selftest:
            return
        try:
            with open(config_path(), "w", encoding="utf-8") as fh:
                json.dump(self.cfg, fh, ensure_ascii=False)
        except Exception:
            pass


PROBE = ("JSON.stringify({title: document.title, inst: document.getElementById('inst').value,"
         " qqq_shown: document.getElementById('qpx').style.display !== 'none', qpx: document.getElementById('qpx').textContent,"
         " px: document.getElementById('px').textContent, tf: document.querySelector('#tfs .on').textContent,"
         " bars: candles.length, bands: bands.filter(Boolean).length,"
         " dot: document.getElementById('dot').title, msg: document.getElementById('msg').textContent})")


def run_selftest(app, result_path):
    time.sleep(12)
    out = {"windows": [], "errors": []}
    # the real path: the page's "+" button -> pywebview.api.new_window() -> a new window
    win0 = app.wins[0]
    win0.evaluate_js("document.getElementById('add').click()")
    time.sleep(6)
    out["click_add"] = {"toast": win0.evaluate_js("document.getElementById('toast').textContent"), "slots": sorted(app.wins)}
    g0 = list(app.geo[0])
    win0.evaluate_js("document.getElementById('tbtn').click()")
    time.sleep(3)
    out["simu_open"] = {"before": g0, "after": list(app.geo[0]), "panel_displayed": win0.evaluate_js("getComputedStyle(document.getElementById('tp')).display"),
                        "chart_width": win0.evaluate_js("Math.round(document.getElementById('cv').getBoundingClientRect().width)")}
    # use the panel while it is open: pick Put, open a position, read it, close it, read the summary
    time.sleep(8)
    js = lambda code: win0.evaluate_js(code)
    if app.cfg.get("srv"):                       # server mode (CI starts simu_server.py in test mode): feed it a price first
        def tick(sym, p):
            req = urllib.request.Request(app.cfg["srv"] + "/api/_test/tick", data=json.dumps({"inst": sym, "p": p}).encode(), headers={"X-Token": app.cfg.get("tok", ""), "Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=10).read()
        try:
            tick("qqq", 100.0)
        except Exception as exc:
            out["errors"].append("srv tick: %r" % (exc,))
        time.sleep(4)
        out["srv_label"] = js("document.getElementById('tpsrv').textContent")
    out["simu_trade"] = {"price_line": js("document.getElementById('tppx').textContent")}
    js("document.getElementById('tpput').click()")
    out["simu_trade"]["put_class"] = js("document.getElementById('tpput').className")
    js("document.getElementById('tpbuy').click()")
    out["simu_trade"]["buy_class"] = js("document.getElementById('tpbuy').className")
    js("document.getElementById('tpput').click()")
    js("document.getElementById('tpgo').click()")
    time.sleep(2)
    out["simu_trade"]["opened"] = js("JSON.stringify([document.getElementById('tp-pos').style.display, document.getElementById('tpdesc').textContent, document.getElementById('tpwarn').textContent, document.getElementById('tproe').textContent])")
    js("document.getElementById('tpend').click()")
    time.sleep(1)
    out["simu_trade"]["summary"] = js("JSON.stringify([document.getElementById('tp-sum').style.display, document.getElementById('sumwhy').textContent, document.getElementById('sumroe').textContent])")
    js("document.getElementById('sumok').click()")
    if app.cfg.get("srv"):                       # a position that the server closes by itself (stop-loss) while the panel is just open
        try:
            js("document.getElementById('tpbuy').click(); document.getElementById('tpsl').value='95'; document.getElementById('tpgo').click()")
            time.sleep(3)
            out["srv_auto"] = {"opened": js("document.getElementById('tpdesc').textContent")}
            tick("qqq", 94.0)
            time.sleep(4)
            out["srv_auto"]["summary"] = js("JSON.stringify([document.getElementById('tp-sum').style.display, document.getElementById('sumwhy').textContent, document.getElementById('sumroe').textContent])")
            js("document.getElementById('sumok').click()")
            time.sleep(2)
        except Exception as exc:
            out["errors"].append("srv_auto: %r" % (exc,))
    win0.evaluate_js("document.getElementById('tbtn').click()")
    time.sleep(3)
    out["simu_closed"] = {"geo": list(app.geo[0]), "panel_displayed": win0.evaluate_js("getComputedStyle(document.getElementById('tp')).display"),
                          "chart_width": win0.evaluate_js("Math.round(document.getElementById('cv').getBoundingClientRect().width)")}
    out["add3"] = app.add_window()
    out["add4_refused"] = app.add_window()
    time.sleep(12)
    # switch the third window's instrument through the drop-down, as a user would
    w2 = app.wins.get(2)
    if w2 is not None:
        w2.evaluate_js("var s=document.getElementById('inst'); s.value='eth'; s.onchange();")
        time.sleep(8)
        out["switch_state"] = {str(k): v for k, v in app.state.items()}
    for slot in sorted(app.wins):
        win = app.wins[slot]
        try:
            r = json.loads(win.evaluate_js(PROBE))
        except Exception as exc:
            r = {"error": repr(exc)}
        r["geo"] = app.geo.get(slot)
        r["slot"] = slot
        out["windows"].append(r)
    # close the middle window on its own, the other two must stay; then a new one takes its place
    mid = app.wins.get(1)
    if mid is not None:
        mid.destroy()
        time.sleep(2)
        out["after_close_slots"] = sorted(app.wins)
        out["readd"] = app.add_window("btc", "1h")
        time.sleep(8)
        out["after_readd_slots"] = sorted(app.wins)
        out["after_readd_pos"] = {str(k): app.geo[k][:2] for k in sorted(app.geo)}
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    for win in list(app.wins.values()):
        try:
            win.destroy()
        except Exception:
            pass


def main():
    selftest = "--selftest" in sys.argv
    result_path = "selftest.json"
    if selftest and len(sys.argv) > sys.argv.index("--selftest") + 1 and not sys.argv[sys.argv.index("--selftest") + 1].startswith("--"):
        result_path = sys.argv[sys.argv.index("--selftest") + 1]
    app = App(selftest, spot="--spot" in sys.argv)
    if selftest:
        app.cfg["srv"] = app.cfg["tok"] = ""
    if selftest and "--srv" in sys.argv:
        app.cfg["srv"] = sys.argv[sys.argv.index("--srv") + 1]
        app.cfg["tok"] = sys.argv[sys.argv.index("--tok") + 1] if "--tok" in sys.argv else ""
    app.open_initial()
    if selftest:
        webview.start(run_selftest, (app, result_path))
    else:
        webview.start()


if __name__ == "__main__":
    main()
