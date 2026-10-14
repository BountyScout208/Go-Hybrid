#!/bin/sh
# install.sh — one-command install of the Hybrid CLI (Discussion #1 ask 2:
# "If install takes more than two commands or I have to build Rust first,
# I'll wait."  This is the answer: one command, no Rust, no sudo.)
#
#   curl -fsSL https://raw.githubusercontent.com/limitlessprojects/Go-Hybrid/main/install.sh | sh
#
# What it does:
#   1. detects platform (linux/darwin, amd64/arm64)
#   2. resolves the LATEST release via the GitHub API (public; no token)
#   3. downloads the matching tarball + verifies its sha256
#   4. extracts to ~/.hybrid (override: HYB_INSTALL_DIR)
#   5. puts a `hyb` launcher on PATH (~/.local/bin, override: HYB_BIN_DIR)
#      — the launcher exports HYB_RUNTIME_DIR so `hyb build` just works
#   6. verifies with `hyb explain HYB-E0002` (exit 0) — NEVER `hyb --help`
#      (usage exits 2 by design; the fleet learned this the hard way)
#
# Idempotent: re-running upgrades in place.
set -eu

REPO="limitlessprojects/Go-Hybrid"
VERSION="${HYB_VERSION:-}"           # pin a version, else latest
INSTALL_DIR="${HYB_INSTALL_DIR:-$HOME/.hybrid}"
BIN_DIR="${HYB_BIN_DIR:-$HOME/.local/bin}"

# --- platform ---
OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
    Linux) OS_ID=linux ;;
    Darwin) OS_ID=darwin ;;
    *) echo "install.sh: unsupported OS '$OS' (prebuilt: linux, darwin)." >&2
       echo "Tell us which platform you need: https://github.com/$REPO/discussions/1" >&2
       exit 1 ;;
esac
case "$ARCH" in
    x86_64) ARCH_ID=amd64 ;;
    aarch64 | arm64) ARCH_ID=arm64 ;;
    *) echo "install.sh: unsupported arch '$ARCH' (prebuilt: amd64, arm64)." >&2
       echo "Tell us which platform you need: https://github.com/$REPO/discussions/1" >&2
       exit 1 ;;
esac
echo "==> platform: $OS_ID/$ARCH_ID"

# --- fetch helper (curl, wget fallback) ---
fetch() {
    url="$1"; out="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url" -o "$out"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$out" "$url"
    else
        echo "install.sh: need curl or wget to download the release" >&2
        exit 1
    fi
}

# --- resolve the release ---
# Primary: the releases/latest REDIRECT (no API, no rate limit):
#   HEAD https://github.com/<repo>/releases/latest -> Location: .../tag/vX.Y.Z
# Fallback: the API (rate-limited 60/hr unauthenticated — documented).
if [ -n "$VERSION" ]; then
    TAG="$VERSION"
else
    echo "==> resolving latest release"
    TAG="$(curl -fsSI "https://github.com/$REPO/releases/latest" 2>/dev/null \
        | sed -n 's/^location:.*\/tag\/\(v[0-9A-Za-z._-]*\).*/\1/ip' | head -1)"
    if [ -z "$TAG" ]; then
        API_JSON="$(mktemp)"
        if fetch "https://api.github.com/repos/$REPO/releases/latest" "$API_JSON" 2>/dev/null; then
            TAG="$(sed -n 's/.*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/p' "$API_JSON" | head -1)"
        fi
        rm -f "$API_JSON"
    fi
    if [ -z "$TAG" ]; then
        echo "install.sh: could not resolve the latest release (network? rate limit?)" >&2
        echo "Pin one manually: HYB_VERSION=v0.1.0 sh install.sh" >&2
        exit 1
    fi
fi
echo "==> release: $TAG"

ASSET="hybrid-${TAG}-${OS_ID}-${ARCH_ID}.tar.gz"
BASE_URL="https://github.com/$REPO/releases/download/${TAG}"

# --- download + verify ---
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "==> downloading $ASSET"
fetch "$BASE_URL/$ASSET" "$TMP/$ASSET"
if fetch "$BASE_URL/$ASSET.sha256" "$TMP/$ASSET.sha256" 2>/dev/null; then
    ( cd "$TMP" && sha256sum -c "$ASSET.sha256" >/dev/null 2>&1 ) \
        || ( cd "$TMP" && shasum -a 256 -c "$ASSET.sha256" >/dev/null 2>&1 ) \
        || { echo "install.sh: sha256 MISMATCH — aborting (do not use the download)" >&2; exit 1; }
    echo "==> sha256 verified"
else
    echo "==> note: no sha256 asset published for this release; skipping verification"
fi

# --- extract ---
mkdir -p "$INSTALL_DIR"
tar xzf "$TMP/$ASSET" -C "$TMP"
rm -rf "$INSTALL_DIR/runtime" "$INSTALL_DIR/bin"
cp -R "$TMP/hybrid-${TAG}/runtime" "$INSTALL_DIR/runtime"
mkdir -p "$INSTALL_DIR/bin"
cp "$TMP/hybrid-${TAG}/hyb" "$INSTALL_DIR/bin/hyb"
chmod +x "$INSTALL_DIR/bin/hyb"

# --- launcher (bakes the install path; runtime override still wins) ---
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/hyb" <<LAUNCH
#!/bin/sh
# Hybrid launcher (installed by install.sh) — wires the packaged runtime.
HYB_HOME="$INSTALL_DIR"
[ -n "\${HYB_INSTALL_DIR:-}" ] && HYB_HOME="\$HYB_INSTALL_DIR"
export HYB_RUNTIME_DIR="\${HYB_RUNTIME_DIR:-\$HYB_HOME/runtime}"
exec "\$HYB_HOME/bin/hyb" "\$@"
LAUNCH
chmod +x "$BIN_DIR/hyb"

# --- verify (hyb explain exits 0; hyb --help exits 2 BY DESIGN) ---
if "$BIN_DIR/hyb" explain E0002 >/dev/null 2>&1; then
    echo "==> installed: $BIN_DIR/hyb  (runtime at $INSTALL_DIR/runtime)"
    case ":$PATH:" in
        *":$BIN_DIR:"*) echo "==> 'hyb' is on PATH already" ;;
        *) echo "==> add to PATH:  export PATH=\"$BIN_DIR:\$PATH\"" ;;
    esac
    echo "==> try:   hyb build program.hy -o app"
    echo "==> docs:  https://github.com/$REPO#readme  ·  claims table: GUARANTEES.md"
else
    echo "install.sh: post-install verification FAILED — the launcher should exit 0 on 'hyb explain E0002'" >&2
    exit 1
fi
