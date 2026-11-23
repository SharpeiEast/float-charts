"""Desktop wrapper for the lead-trader page: web/leader_float.html in a native window (same pywebview route as qqqusdt-float / ashare-float).

The page itself (left: action table, right: K-line with the traders' real entries/exits) is bundled; its data comes from the
leader-tracker server (server/leader_tracker.py, runs 24/7 on us01a) whose address is cfg["srv"] (Tailscale address pre-filled).

Run:        python app/main.py [--srv http://host:8780]
Self-test:  python app/main.py --selftest result.json   (opens the window, waits, writes what the page shows, exits)
"""
import json
import os
import sys
import threading
import time
import urllib.parse

import webview

DEFAULT_SRV = "http://100.78.175.68:8780"
DEFAULT = {"width": 1280, "height": 720, "x": 60, "y": 40, "srv": DEFAULT_SRV, "on_top": False}


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
    d = os.path.join(root, "leader-float")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "window.json")


def load_cfg():
    try:
        with open(config_path(), encoding="utf-8") as fh:
            return {**DEFAULT, **json.load(fh)}
    except Exception:
        return dict(DEFAULT)


def save_cfg(cfg):
    try:
        with open(config_path(), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False)
    except Exception:
        pass


def main():
    cfg = load_cfg()
    if "--srv" in sys.argv:
        cfg["srv"] = sys.argv[sys.argv.index("--srv") + 1]
    selftest = "--selftest" in sys.argv
    out_path = "selftest.json"
    if selftest and len(sys.argv) > sys.argv.index("--selftest") + 1 and not sys.argv[sys.argv.index("--selftest") + 1].startswith("--"):
        out_path = sys.argv[sys.argv.index("--selftest") + 1]
    url = resource(os.path.join("web", "leader_float.html")) + "?srv=" + urllib.parse.quote(cfg["srv"], safe="")
    geo = [cfg["x"], cfg["y"], cfg["width"], cfg["height"]]
    win = webview.create_window("盯盘 · 带单员动作", url=url, width=int(cfg["width"]), height=int(cfg["height"]), x=int(cfg["x"]), y=int(cfg["y"]),
                                min_size=(820, 480), on_top=bool(cfg.get("on_top")))

    def moved(x, y):
        geo[0], geo[1] = x, y

    def resized(w, h):
        geo[2], geo[3] = w, h

    def closing():
        save_cfg({**cfg, "x": geo[0], "y": geo[1], "width": geo[2], "height": geo[3]})

    win.events.moved += moved
    win.events.resized += resized
    win.events.closing += closing
    if selftest:
        def run():
            time.sleep(12)
            res = {"errors": []}
            for k, js in (("title", "document.title"), ("status", "document.getElementById('status').textContent"),
                          ("buttons", "document.querySelectorAll('#toolbar button').length"),
                          ("traders", "document.querySelectorAll('#ddpanel label').length"),
                          ("canvas", "document.querySelectorAll('#chart canvas').length")):
                try:
                    res[k] = win.evaluate_js(js)
                except Exception as exc:
                    res["errors"].append("%s: %r" % (k, exc))
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(res, fh, ensure_ascii=False)
            win.destroy()
        threading.Thread(target=run, daemon=True).start()
    webview.start()


if __name__ == "__main__":
    main()
