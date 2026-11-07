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

import webview

MAX_WINS = 3
DEFAULT_INSTS = ["qqq", "eth", "btc"]
DEFAULT = {"width": 560, "height": 360, "x": 80, "y": 40, "slots": [{"inst": "qqq", "tf": "15m"}]}
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
            cfg = {**DEFAULT, **json.load(fh)}
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


class App:
    def __init__(self, selftest=False, spot=False):
        self.selftest = selftest
        self.spot = spot
        self.cfg = load_cfg()
        self.lock = threading.RLock()
        self.wins = {}        # slot -> window
        self.state = {}       # slot -> {"inst", "tf"}
        self.geo = {}         # slot -> [x, y, width, height], kept up to date from the moved/resized events (never read the window's properties from event handlers or other threads)
        self.html = resource(os.path.join("web", "qqqusdt_float.html"))

    # ---- window creation ----
    def _create(self, slot, inst, tf, x, y):
        url = self.html + "?inst=" + str(inst) + "&tf=" + str(tf) + ("&market=spot" if self.spot else "")
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
            slots = [{"inst": "qqq", "tf": "15m"}]
        for i, s in enumerate(slots):
            y = int(self.cfg["y"]) + i * (int(self.cfg["height"]) + GAP)
            self._create(i, s["inst"], s.get("tf", "15m"), self.cfg["x"], y)

    def add_window(self, inst=None, tf="15m"):
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
            self._save_slots()
        log("closed slot=%s, open now: %s" % (slot, sorted(self.wins)))

    def _save_geometry(self, slot):
        try:
            g = self.geo.get(slot)
            if g:
                self.cfg["width"], self.cfg["height"] = g[2], g[3]
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
         " qqq_shown: document.getElementById('qq').style.display !== 'none', qpx: document.getElementById('qpx').textContent,"
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
    app.open_initial()
    if selftest:
        webview.start(run_selftest, (app, result_path))
    else:
        webview.start()


if __name__ == "__main__":
    main()
