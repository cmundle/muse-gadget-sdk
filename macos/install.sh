#!/usr/bin/env bash
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Install the macOS Device SDK.
#
# Run this as your normal user (not root): it only asks for sudo to create
# /opt/musegadget and the /usr/local/bin symlink. Everything else, including
# the Muse's state and the LaunchAgent, belongs to you.
#
#   bash install.sh [--from SOURCE] [--sdk-token TOKEN] [--yes] [--no-pair]
#   bash install.sh --uninstall [--purge]
#
# Unlike the Linux installer, nothing here needs root at runtime: the service
# runs as you, Bluetooth pairing uses CoreBluetooth (no BlueZ), and the
# background service is a per-user LaunchAgent instead of a systemd unit.
# By default the LaunchAgent is installed but NOT loaded: start with the
# terminal (`musegadget run`) and enable the agent once pairing is stable.

set -euo pipefail

PREFIX="/opt/musegadget"
VENV="$PREFIX/venv"
SYMLINK="/usr/local/bin/musegadget"
PLIST_NAME="com.muse.gadget.plist"
PLIST="$HOME/Library/LaunchAgents/$PLIST_NAME"
STATE_DIR="$HOME/Library/Application Support/musegadget"
DEFAULT_SOURCE="git+https://github.com/facebookincubator/muse-gadget-sdk@main#subdirectory=macos"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '2,/^set -euo/p' "$0" | grep -E '^#' | grep -v 'Copyright\|License\|http\|^#$' | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        sudo "$@"
    fi
}

check_system() {
    [ "$(uname -s)" = "Darwin" ] || die "this installer is for macOS only."
    if command -v sw_vers >/dev/null; then
        major="$(sw_vers -productVersion | cut -d. -f1)"
        if [ "${major:-0}" -lt 13 ]; then
            warn "macOS $major detected; 13 (Ventura) or later is recommended."
        fi
    fi
    command -v python3 >/dev/null || die "python3 not found. Install it from python.org or with brew."
    pyver="$(python3 -c 'import sys; print(sys.version_info[0] * 10 + sys.version_info[1])')"
    [ "$pyver" -ge 39 ] || die "Python 3.9 or later is required (found $pyver)."
    if ! command -v brew >/dev/null; then
        warn "Homebrew not found; it is handy but not required for this install."
    fi
    if [ "$(id -u)" -eq 0 ]; then
        die "run this as your normal user, not root: it uses sudo only where needed."
    fi
    if ! system_profiler SPBluetoothDataType 2>/dev/null | grep -q "Bluetooth:"; then
        warn "no Bluetooth hardware detected. You can install, but pairing with the Muse app needs Bluetooth."
    fi
}

install_musegadget() {
    if [ ! -d "$PREFIX" ]; then
        say "Creating $PREFIX (needs administrator approval)"
        as_root mkdir -p "$PREFIX"
        as_root chown "$(id -un):staff" "$PREFIX"
    fi
    if [ ! -x "$VENV/bin/python" ]; then
        say "Creating $VENV"
        python3 -m venv "$VENV"
    fi
    say "Installing musegadget from $SOURCE"
    # No --require-hashes on macOS: the Linux hash-pinned lock targets
    # Debian wheels, so macOS installs resolve normally from pip.
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet --no-deps --force-reinstall "$SOURCE"
    "$VENV/bin/pip" install --quiet "$SOURCE"
    if [ -w "$(dirname "$SYMLINK")" ]; then
        ln -sf "$VENV/bin/musegadget" "$SYMLINK"
    else
        say "Linking $SYMLINK (needs administrator approval)"
        as_root ln -sf "$VENV/bin/musegadget" "$SYMLINK"
    fi
}

save_sdk_token() {
    if [ -z "$SDK_TOKEN" ]; then
        if [ ! -s "$STATE_DIR/sdk_token" ]; then
            say "No SDK token yet. Get one at gadgets.muse.ai and rerun with --sdk-token; gadgets without one will stop pairing."
        fi
        return 0
    fi
    say "Saving your SDK token"
    mkdir -p "$STATE_DIR"
    chmod 0700 "$STATE_DIR"
    printf '%s\n' "$SDK_TOKEN" > "$STATE_DIR/sdk_token"
    chmod 0600 "$STATE_DIR/sdk_token"
}

install_launch_agent() {
    data="$("$VENV/bin/python" -c 'import musegadget, os; print(os.path.join(os.path.dirname(musegadget.__file__), "data"))')"
    mkdir -p "$HOME/Library/LaunchAgents"
    # Point the plist at this install's venv and the user's log directory.
    sed -e "s|@PREFIX@|$PREFIX|" -e "s|@HOME@|$HOME|" "$data/$PLIST_NAME" > "$PLIST"
    say "Installed $PLIST but did not load it."
    say "Start in the terminal first: musegadget run"
    say "Once pairing is stable, enable the background service with:"
    say "  launchctl load -w $PLIST"
}

ask() {
    [ "$ASSUME_YES" = 1 ] && return 0
    printf '%s [y/N] ' "$1"
    read -r answer < /dev/tty
    [ "$answer" = "y" ] || [ "$answer" = "Y" ]
}

pair() {
    if [ -s "$STATE_DIR/pairing.json" ]; then
        say "Already paired; the service will reconnect to your Muse."
        return
    fi
    if [ "$NO_PAIR" = 1 ]; then
        say "Skipping pairing. Run 'musegadget pair' when you're ready."
        return
    fi
    cat <<'EOF'
When the installer says pairing is open, go to the Muse app:
  1. Turn on Settings > Devices > Developer mode.
  2. Add a device and choose the device named below.
  3. When asked for Wi-Fi, pick the network shown; no password is needed.
macOS will ask for Bluetooth permission the first time; allow it.
EOF
    "$VENV/bin/musegadget" pair || warn "not paired. Run 'musegadget pair' to try again."
}

summary() {
    echo
    "$VENV/bin/musegadget" info
    echo
    say "Done. Your Muse will run commands on this Mac as '$(id -un)', with that account's permissions."
    say "Terminal-first: run 'musegadget run' in the foreground and watch the log."
}

uninstall() {
    launchctl unload -w "$PLIST" 2>/dev/null || true
    rm -f "$PLIST" "$SYMLINK"
    as_root rm -rf "$PREFIX"
    if [ "$PURGE" = 1 ]; then
        rm -rf "$STATE_DIR"
        say "Removed the device identity and pairing too. Remove the device in the Muse app as well."
    else
        say "Kept the device identity and pairing in $STATE_DIR (use --purge to remove them)."
    fi
}

main() {
    SOURCE="$DEFAULT_SOURCE" SDK_TOKEN="" ASSUME_YES=0 NO_PAIR=0 UNINSTALL=0 PURGE=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --from) SOURCE="${2:?--from needs a value}"; shift 2 ;;
            --sdk-token) SDK_TOKEN="${2:?--sdk-token needs a value}"; shift 2 ;;
            --yes|-y) ASSUME_YES=1; shift ;;
            --no-pair) NO_PAIR=1; shift ;;
            --uninstall) UNINSTALL=1; shift ;;
            --purge) PURGE=1; shift ;;
            -h|--help) usage 0 ;;
            *) usage 1 >&2; die "unknown option: $1" ;;
        esac
    done

    if [ "$UNINSTALL" = 1 ]; then uninstall; return; fi

    if [ -n "$SDK_TOKEN" ] && ! [[ "$SDK_TOKEN" =~ ^mgst_[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$ ]]; then
        die "that SDK token is not valid; copy it again from gadgets.muse.ai."
    fi

    if [ -d "$SOURCE" ]; then SOURCE="$(cd "$SOURCE" && pwd)"; fi

    check_system
    if [ "$ASSUME_YES" != 1 ]; then
        say "Your Muse will be able to run any command on this Mac as '$(id -un)', with that account's permissions."
        ask "Continue?" || die "cancelled."
    fi
    install_musegadget
    save_sdk_token
    install_launch_agent
    pair
    summary
}

# Everything runs from main, so a truncated download never runs a partial script.
main "$@"
