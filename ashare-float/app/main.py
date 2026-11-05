"""Desktop wrapper for the A-share candlestick page: web/ashare_float.html in native always-on-top windows.

Up to MAX_WINS windows (one stock each). A new window opens right below the lowest existing one; every window can be
closed on its own. The page fetches Eastmoney data directly from this computer (no server of ours in between).

Run:        python app/main.py
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
DEFAULT_CODES = ["159577", "161130", "513850"]
DEFAULT = {"width": 560, "height": 300, "x": 80, "y": 40, "slots": [{"code": "159577", "tf": "1d"}]}
GAP = 30 if sys.platform == "darwin" else 4          # title-bar allowance between stacked windows


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
    d = os.path.join(root, "ashare-float")
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
        cfg["slots"] = [s for s in cfg.get("slots", []) if isinstance(s, dict) and s.get("code")][:MAX_WINS] or DEFAULT["slots"]
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

    def set_state(self, code, tf, name):
        self._app.set_state(self._slot, code, tf, name)
        return True


class App:
    def __init__(self, selftest=False):
        self.selftest = selftest
        self.cfg = load_cfg()
        self.lock = threading.RLock()
        self.wins = {}        # slot -> window
        self.state = {}       # slot -> {"code", "tf"}
        self.geo = {}         # slot -> [x, y, width, height], kept up to date from the moved/resized events (never read the window's properties from event handlers or other threads)
        self.html = resource(os.path.join("web", "ashare_float.html"))

    # ---- window creation ----
    def _create(self, slot, code, tf, x, y):
        url = self.html + "?code=" + str(code) + "&tf=" + str(tf)
        win = webview.create_window("A股 " + str(code), url=url, js_api=SlotApi(self, slot),
                                    width=int(self.cfg["width"]), height=int(self.cfg["height"]),
                                    x=int(x), y=int(y), min_size=(380, 220), on_top=True)
        self.wins[slot] = win
        self.state[slot] = {"code": str(code), "tf": str(tf)}
        self.geo[slot] = [int(x), int(y), int(self.cfg["width"]), int(self.cfg["height"])]
        win.events.closed += lambda: self._closed(slot)
        win.events.moved += lambda mx, my: self._geo(slot, 0, mx, my)
        win.events.resized += lambda mw, mh: self._geo(slot, 2, mw, mh)
        win.events.closing += lambda: self._save_geometry(slot)
        return win

    def open_initial(self):
        slots = self.cfg["slots"][:MAX_WINS]
        if self.selftest:
            slots = [{"code": "159577", "tf": "1h"}]
        for i, s in enumerate(slots):
            y = int(self.cfg["y"]) + i * (int(self.cfg["height"]) + GAP)
            self._create(i, s["code"], s.get("tf", "1d"), self.cfg["x"], y)

    def add_window(self, code=None, tf="1d"):
        with self.lock:
            free = [s for s in range(MAX_WINS) if s not in self.wins]
            if not free:
                return {"ok": False, "msg": "最多同时打开 %d 个窗口, 请先关闭一个" % MAX_WINS}
            slot = free[0]
            used = {s["code"] for s in self.state.values()}
            if not code:
                code = next((c for c in DEFAULT_CODES if c not in used), DEFAULT_CODES[0])
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
            log("add_window slot=%s code=%s at (%s, %s)" % (slot, code, x, y))
            self._create(slot, code, tf, x, y)
            self._save_slots()
            return {"ok": True, "slot": slot}

    # ---- bookkeeping ----
    def set_state(self, slot, code, tf, name):
        with self.lock:
            if slot in self.state:
                self.state[slot] = {"code": code, "tf": tf}
            win = self.wins.get(slot)
            self._save_slots()
        if win is not None:
            try:
                win.set_title(("%s " % name if name else "") + code)
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
            self.state.pop(slot, None)       # keep the closed window's code out of the saved list
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


PROBE = ("JSON.stringify({title: document.title, code: document.getElementById('code').value,"
         " name: document.getElementById('name').textContent, px: document.getElementById('px').textContent,"
         " chg: document.getElementById('chg').textContent, tf: document.querySelector('#tfs .on').textContent,"
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
    out["add3"] = app.add_window("513850", "1m")
    out["add4_refused"] = app.add_window()
    time.sleep(12)
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
        out["readd"] = app.add_window("600519", "1d")
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
    if selftest and len(sys.argv) > sys.argv.index("--selftest") + 1:
        result_path = sys.argv[sys.argv.index("--selftest") + 1]
    app = App(selftest)
    app.open_initial()
    if selftest:
        webview.start(run_selftest, (app, result_path))
    else:
        webview.start()


if __name__ == "__main__":
    main()
