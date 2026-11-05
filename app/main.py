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

import webview

MAX_WINS = 3
DEFAULT_CODES = ["513850", "159577", "161130"]
DEFAULT = {"width": 560, "height": 300, "x": 80, "y": 40, "slots": [{"code": "513850", "tf": "1d"}]}
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
        return self._app.add_window()

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
        self.html = resource(os.path.join("web", "ashare_float.html"))

    # ---- window creation ----
    def _create(self, slot, code, tf, x, y):
        url = self.html + "?code=" + str(code) + "&tf=" + str(tf)
        win = webview.create_window("A股 " + str(code), url=url, js_api=SlotApi(self, slot),
                                    width=int(self.cfg["width"]), height=int(self.cfg["height"]),
                                    x=int(x), y=int(y), min_size=(380, 220), on_top=True)
        self.wins[slot] = win
        self.state[slot] = {"code": str(code), "tf": str(tf)}
        win.events.closed += lambda: self._closed(slot)
        if not self.selftest:
            win.events.closing += lambda: self._save_geometry(win)
        return win

    def open_initial(self):
        slots = self.cfg["slots"][:MAX_WINS]
        if self.selftest:
            slots = [{"code": "159577", "tf": "1d"}]
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
            above = [s for s in self.wins if s < slot]
            below = [s for s in self.wins if s > slot]
            if above:                                   # directly under the nearest window above it (fills a gap left by a closed window)
                ref = self.wins[max(above)]
                x, y = ref.x, ref.y + ref.height + GAP
            elif below:                                 # slot 0 was closed: take its place above the others
                ref = self.wins[min(below)]
                x, y = ref.x, max(0, ref.y - pitch)
            else:
                x, y = self.cfg["x"], self.cfg["y"]
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

    def _closed(self, slot):
        with self.lock:
            self.wins.pop(slot, None)
            if not self.selftest:
                # keep the closed window's code out of the saved list
                self.state.pop(slot, None)
                self._save_slots()
            else:
                self.state.pop(slot, None)

    def _save_geometry(self, win):
        try:
            self.cfg["width"], self.cfg["height"] = int(win.width), int(win.height)
            top = min(self.wins.values(), key=lambda w: w.y)
            self.cfg["x"], self.cfg["y"] = int(top.x), int(top.y)
        except Exception:
            pass
        self._write()

    def _save_slots(self):
        if self.selftest:
            return
        self.cfg["slots"] = [self.state[s] for s in sorted(self.state)][:MAX_WINS]
        self._write()

    def _write(self):
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
    # open two more windows (minute and weekly view), then a 4th that must be refused
    out["add2"] = app.add_window("513850", "1m")
    out["add3"] = app.add_window("161130", "1w")
    out["add4_refused"] = app.add_window()
    time.sleep(12)
    for slot in sorted(app.wins):
        win = app.wins[slot]
        try:
            r = json.loads(win.evaluate_js(PROBE))
        except Exception as exc:
            r = {"error": repr(exc)}
        try:
            r.update(x=win.x, y=win.y, w=win.width, h=win.height)
        except Exception:
            pass
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
        out["after_readd_pos"] = {str(k): [app.wins[k].x, app.wins[k].y] for k in sorted(app.wins)}
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
