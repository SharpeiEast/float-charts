# Leader Float

Binance copy-trading lead traders (16 watched) on BTCUSDT / ETHUSDT: left a table of their actions, right a K-line chart with their real entries / exits.
Same route as qqqusdt-float: single-file HTML in a pywebview window, built by CI for Windows / macOS.

- `web/leader_float.html` + `web/lwc.js` (lightweight-charts 4.1.3): the page. Opened directly in a browser through the server it also works.
- `app/main.py`: native window, geometry remembered, server address in `%APPDATA%/leader-float/window.json` (`srv`, default Tailscale `http://100.78.175.68:8780`).
- `server/leader_tracker.py`: collector + API + page; runs 24/7 on us01a (`leader-tracker.service`, port 8780, SQLite in `data/`, nightly backup to m03). It stores every fill of the 16 traders (Binance keeps only the latest ~6000) and the position history (months back).
  Deploy: copy `leader_tracker.py`, `leader_float.html` (as `index.html`) and `lwc.js` to `/srv/shared/fund/leader_tracker/`, then `systemctl --user restart leader-tracker`.
- Release: tag `leader-vX.Y.Z`.
