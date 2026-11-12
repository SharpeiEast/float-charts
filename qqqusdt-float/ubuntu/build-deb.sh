#!/usr/bin/env bash
# Build qqqusdt-float_<version>_all.deb (Ubuntu / Debian desktop). Needs python3 + pip + dpkg-deb.
# The package uses the system GTK + WebKit (python3-gi, gir1.2-webkit2-4.1) and carries pywebview and its small pure-python
# dependencies under /opt/qqqusdt-float/lib, so it does not depend on the distribution's pywebview version.
set -euo pipefail
VER="${1:?usage: build-deb.sh <version, e.g. 0.2.2>}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${2:-$HERE/out}"
ROOT="$(mktemp -d)"
PKG="$ROOT/qqqusdt-float_${VER}_all"
mkdir -p "$PKG/DEBIAN" "$PKG/opt/qqqusdt-float" "$PKG/usr/bin" "$PKG/usr/share/applications" "$OUT"
cp -r "$HERE/app" "$HERE/web" "$PKG/opt/qqqusdt-float/"
find "$PKG" -name __pycache__ -prune -exec rm -rf {} +
python3 -m pip install --quiet --no-compile --target "$PKG/opt/qqqusdt-float/lib" "pywebview>=5.3"
cat > "$PKG/usr/bin/qqqusdt-float" <<'LAUNCH'
#!/bin/sh
# GDK_BACKEND=x11 (XWayland) lets the app widen/move its own window for the SIMU panel, which native Wayland does not allow
export PYTHONPATH=/opt/qqqusdt-float/lib${PYTHONPATH:+:$PYTHONPATH}
[ -n "$DISPLAY" ] && export GDK_BACKEND="${GDK_BACKEND:-x11}"
exec python3 /opt/qqqusdt-float/app/main.py "$@"
LAUNCH
chmod 755 "$PKG/usr/bin/qqqusdt-float"
cat > "$PKG/usr/share/applications/qqqusdt-float.desktop" <<'DESK'
[Desktop Entry]
Type=Application
Name=QQQUSDT Float
Comment=Floating candlestick charts (QQQUSDT / ETH / BTC) with a simulated contract trading panel
Exec=qqqusdt-float
Terminal=false
Categories=Office;Finance;
DESK
cat > "$PKG/DEBIAN/control" <<CTRL
Package: qqqusdt-float
Version: ${VER}
Architecture: all
Maintainer: qqqusdt-float
Depends: python3 (>= 3.9), python3-gi, gir1.2-gtk-3.0, gir1.2-webkit2-4.1 | gir1.2-webkit2-4.0
Section: utils
Priority: optional
Description: QQQUSDT / ETH / BTC floating candlestick charts
 Real-time candlesticks with Bollinger bands and a simulated contract trading panel (SIMU).
CTRL
dpkg-deb --build --root-owner-group "$PKG" "$OUT/qqqusdt-float_${VER}_all.deb" >/dev/null
echo "built $OUT/qqqusdt-float_${VER}_all.deb"
rm -rf "$ROOT"
