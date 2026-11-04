"""Desktop wrapper for the float page: the same web/qqqusdt_float.html shown in a native always-on-top window.

The page talks to Binance and Yahoo directly from this computer (nothing goes through a server of ours).
Run:        python app/main.py
Self-test:  python app/main.py --selftest result.json   (opens the window, waits, writes what it saw, exits)
"""
import json
import os
import sys
import time

import webview

TITLE = "QQQUSDT"
DEFAULT = {"width": 560, "height": 360, "x": None, "y": None}


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
    return os.path.join(d, "window.json")


def load_cfg():
    try:
        with open(config_path(), encoding="utf-8") as fh:
            return {**DEFAULT, **json.load(fh)}
    except Exception:
        return dict(DEFAULT)


def save_cfg(window):
    try:
        cfg = {"width": int(window.width), "height": int(window.height), "x": int(window.x), "y": int(window.y)}
        with open(config_path(), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
    except Exception:
        pass


def main():
    selftest = "--selftest" in sys.argv
    result_path = sys.argv[sys.argv.index("--selftest") + 1] if selftest and len(sys.argv) > sys.argv.index("--selftest") + 1 else "selftest.json"
    market = "spot" if "--spot" in sys.argv else "perp"
    cfg = load_cfg()
    html = resource(os.path.join("web", "qqqusdt_float.html"))
    kwargs = dict(width=cfg["width"], height=cfg["height"], min_size=(360, 240), on_top=True)
    if cfg["x"] is not None and cfg["y"] is not None and not selftest:
        kwargs.update(x=cfg["x"], y=cfg["y"])
    window = webview.create_window(TITLE, url=html + ("?market=spot" if market == "spot" else ""), **kwargs)

    def on_loaded():
        # the page offers its own "浮窗" button for browsers; the app window is already on top
        window.evaluate_js("var b=document.getElementById('pip'); if (b) b.style.display='none';")

    window.events.loaded += on_loaded
    if not selftest:
        window.events.closing += lambda: save_cfg(window)

    def run_selftest():
        time.sleep(12)
        out = {}
        try:
            out = window.evaluate_js("JSON.stringify({title: document.title, qqq: document.getElementById('qpx').textContent,"
                                     " px: document.getElementById('px').textContent, tf: document.querySelector('#tfs .on').textContent,"
                                     " dot: document.getElementById('dot').title, msg: document.getElementById('msg').textContent})")
            out = json.loads(out) if isinstance(out, str) else out
        except Exception as exc:
            out = {"error": repr(exc)}
        with open(result_path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False)
        window.destroy()

    webview.start(run_selftest if selftest else None)


if __name__ == "__main__":
    main()
