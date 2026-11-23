#!/usr/bin/env bash
# Build leader-float_<version>_all.deb (Ubuntu / Debian desktop). Needs python3 + pip + dpkg-deb.
# The package uses the system GTK + WebKit (python3-gi, gir1.2-webkit2-4.1) and carries pywebview and its small pure-python
# dependencies under /opt/leader-float/lib, so it does not depend on the distribution's pywebview version.
set -euo pipefail
VER="${1:?usage: build-deb.sh <version, e.g. 0.2.2>}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${2:-$HERE/out}"
ROOT="$(mktemp -d)"
PKG="$ROOT/leader-float_${VER}_all"
mkdir -p "$PKG/DEBIAN" "$PKG/opt/leader-float" "$PKG/usr/bin" "$PKG/usr/share/applications" "$OUT"
cp -r "$HERE/app" "$HERE/web" "$PKG/opt/leader-float/"
find "$PKG" -name __pycache__ -prune -exec rm -rf {} +
python3 -m pip install --quiet --no-compile --target "$PKG/opt/leader-float/lib" "pywebview>=5.3"
cat > "$PKG/usr/bin/leader-float" <<'LAUNCH'
#!/bin/sh
export PYTHONPATH=/opt/leader-float/lib${PYTHONPATH:+:$PYTHONPATH}
exec python3 /opt/leader-float/app/main.py "$@"
LAUNCH
chmod 755 "$PKG/usr/bin/leader-float"
cat > "$PKG/usr/share/applications/leader-float.desktop" <<'DESK'
[Desktop Entry]
Type=Application
Name=Leader Float
Comment=Binance lead traders (BTC / ETH): action table and K-line with their real entries and exits
Exec=leader-float
Terminal=false
Categories=Office;Finance;
DESK
cat > "$PKG/DEBIAN/control" <<CTRL
Package: leader-float
Version: ${VER}
Architecture: all
Maintainer: leader-float
Depends: python3 (>= 3.9), python3-gi, gir1.2-gtk-3.0, gir1.2-webkit2-4.1 | gir1.2-webkit2-4.0
Section: utils
Priority: optional
Description: Binance lead traders: action table and K-line with their real entries and exits
 Data comes from the leader-tracker server (default Tailscale address, see window.json).
CTRL
dpkg-deb --build --root-owner-group "$PKG" "$OUT/leader-float_${VER}_all.deb" >/dev/null
echo "built $OUT/leader-float_${VER}_all.deb"
rm -rf "$ROOT"
