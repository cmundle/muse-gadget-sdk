# AGENTS.md

How to work on this macOS client, test it, and deploy it to a Mac. See
`README.md` for a shorter human overview.

## What this is

A Python package (`musegadget`) that makes any Mac into a Muse
Home Link. It pairs with the Muse phone app over BLE (CoreBluetooth), then
holds an encrypted Noise session to the user's Muse VM and runs the
commands Muse sends it. It is the macOS counterpart of the Linux client in
`../linux` and the ESP32 firmware in `../esp32`, and speaks the same
pairing and control protocols, so the same app pairs any of them.

| Module | Role |
|---|---|
| `cli.py` | `musegadget pair`, `run`, `send-user-msg`, `info`, `unpair`, `appletv-pair` |
| `pairing.py` | Community pairing v5: P-256 ECDH, HKDF-SHA256, AES-256-GCM, `confirm_app` |
| `ble_framing.py` | Chunked BLE framing (`0xFE`, index, total, payload) |
| `ble_setup.py` | Setup commands behind the GATT characteristics (transport-agnostic) |
| `ble_server_macos.py` | CoreBluetooth GATT peripheral and advertisement (replaces Linux `ble_server.py`) |
| `identity.py` | Persistent identity: `homelink-xxxxxx` node id, `MuseGadgetXXXXXX` BLE name |
| `muse_api.py` | `fetch_vms` and device token refresh |
| `link_client.py` | One session: `/v1/noise` upgrade, Noise XX, `/link-control`, `/chat/stream` |
| `service.py` | `musegadget run`: reconnect loop, token rotation, local socket |
| `executor.py`, `fileops.py` | The commands Muse can run, as the installing user |
| `hometheater.py` | Apple TV / HomePod (pyatv) and Sonos (SoCo) commands; optional deps, lazy imports |
| `config.py` | State dir, socket path, SDK token (macOS locations) |
| `network.py` | Connectivity checks; Wi-Fi SSID via `networksetup` on macOS |
| `noise/` | Noise XX handshake, framing and service envelopes |
| `data/` | The LaunchAgent plist shipped in the package |

## Prerequisites

- Python 3.9 or later.
- macOS 13 (Ventura) or later for the real device; the pure-Python parts and
  the test suite run anywhere.
- For a real device: Bluetooth, and `pyobjc-framework-CoreBluetooth` (a pip
  dependency). macOS prompts for Bluetooth permission on first use.
- The tests need no Bluetooth, device or network:
  `python3 -m pytest` from this directory.

## Differences from the Linux client

- **No root, ever.** The Linux service runs as root and drops privileges to
  the run-as account for commands. On macOS everything runs as the
  installing user: `Executor._child_options()` sets a clean environment but
  performs no setuid/setgid, and `cli.py`'s `run` ignores `--run-as` unless
  it names the current account.
- **CoreBluetooth instead of BlueZ.** `ble_server_macos.py` replaces
  `ble_server.py` (BlueZ GATT over D-Bus + GLib). Same UUIDs, same
  Transport interface (`send_packets`, `mtu`, `disconnect`), same
  `MuseGadgetXXXXXX` advertisement name. No MTU config file: CoreBluetooth
  negotiates ATT MTU itself, and TX packets stay at the framing layer's
  160-byte maximum like the Linux build.
- **LaunchAgent instead of systemd.** `data/com.muse.gadget.plist` is a
  per-user agent (`~/Library/LaunchAgents`), `RunAtLoad` false by default:
  the terminal-first workflow is `musegadget run` in the foreground, then
  `launchctl load -w` once stable. Logs go to
  `~/Library/Logs/musegadget.log`.
- **State in Application Support.** `~/Library/Application Support/musegadget`
  holds `identity.json`, `pairing.json` and `sdk_token` (0600/0700
  permissions, owned by the user). The local socket for
  `musegadget send-user-msg` lives next to the state (`musegadget.sock`,
  mode 0600) instead of `/run`.
- **No hash-pinned requirements.** The Linux installer pip-installs with
  `--require-hashes` against Debian wheels; macOS resolves normally from
  pip (documented in `install.sh`).
- **`device.health` without `/proc`.** Uptime from `kern.boottime`, memory
  from `hw.memsize` + `vm_stat`, model from `hw.model`. Temperature is
  omitted: no public macOS API exposes it to unprivileged processes.
- **`network.active_wifi_ssid`** tries `nmcli`, then macOS
  `networksetup -getairportnetwork en0`, then gives up.

## Tests

```sh
python3 -m pytest
```

The tests need no Bluetooth, device or network. Tests for the CoreBluetooth
peripheral (`test_ble_server_macos.py`) exercise the Transport interface
(`mtu`, packet pacing, UUID constants) with the Bluetooth layer stubbed;
they skip entirely when the module cannot exercise real hardware, and the
hardware paths are marked as untested in `ble_server_macos.py`.

## Deploy to a Mac

Copy this directory to the Mac and install from it:

```sh
bash install.sh --from .            # asks before continuing
bash install.sh --from . --yes      # doesn't ask
bash install.sh --from . --no-pair  # install now, pair later
```

Reinstalling keeps the pairing. The foreground `musegadget run` is killed by
Ctrl-C; the LaunchAgent (once loaded) restarts the service, which kills any
command Muse is running at that moment.

Installer flags: `--sdk-token TOKEN`, `--yes`, `--no-pair`, `--from SOURCE`,
`--uninstall`, `--purge`. The installer is written ShellCheck-clean; keep it
that way.

## Run and debug

```sh
musegadget run -v            # foreground service, verbose
musegadget info              # identity and pairing state
musegadget pair              # open the 10-minute pairing window
tail -f ~/Library/Logs/musegadget.log   # background service log
launchctl load -w ~/Library/LaunchAgents/com.muse.gadget.plist
launchctl unload -w ~/Library/LaunchAgents/com.muse.gadget.plist
```

State lives in `~/Library/Application Support/musegadget` (mode 0700):
`identity.json` survives unpairing, `pairing.json` holds the device tokens.

A healthy start logs `commands run as `, `Noise session established`,
`sent link.register` and `registered with the Muse`.
Each command the Muse runs logs `invoke <command>`, then how it ended, such as
`system.run ok, exit 0 in 41 ms` or `file.read failed in 3 ms`. The log
never has a command's parameters, output or error message.

## Pairing

- Community mode only: `pairing_auth: "none"`, epoch 0, policy `confirm_app`.
  A Mac has no button, so the app's own confirmation stands in for it, and
  BLE only advertises while `musegadget pair` runs.
- The BLE name is `MuseGadget` plus the last six hex digits of the identity,
  with **no hyphen**: the apps compare the text after the prefix with the text
  after `homelink-` in the node id. `get_device_info` must report
  `model: "hatch_link"`.
- The apps always scan Wi-Fi and send `provision_v2` with an SSID. When the
  device is online it offers one open network, "the current connection", and
  ignores the Wi-Fi fields it gets back. It never stores them.
- `provision_v2` carries `api_url`, which only older firmware reads (it adds
  `/hatch/`). This client ignores it and uses `api_url_v2` when newer apps
  send it, or `https://api.muse.ai`, with bare API paths either way.
- Answer plaintext `get_device_info` at any time. Android re-sends it when it
  restarts a handshake on the same connection.
- GATT status 133 on the phone is usually stale Bluetooth state on the phone.
  Toggling the phone's Bluetooth clears it.

## Talking to the Muse

- Connect to `wss:///v1/noise?vm_id=` with the per-VM bearer
  from `fetch_vms` in an `Authorization` header. A 401 or 403 on the upgrade
  means fetch fresh VM credentials, not retry the same bearer.
- After the Noise handshake, open `POST /link-control` and leave the body open.
  Both directions carry JSON messages, each prefixed with a little-endian u32
  length. The device sends `link.register`, then a `link.result` for each
  `link.invoke`. `link.unpaired` means the Muse removed the device.
- Register as `platform: "macos"`, `device_family: "homehub"`. Never use family
  `link` or advertise `device.ota`: the server pushes ESP32 firmware updates to
  every `link` device.
- Messages from the device to the Muse (`musegadget send-user-msg`) go as separate
  `POST /chat/stream` requests on the same session, with `device_id` set to the
  node id. `session_id` picks the chat; `chat_id` is not an API field and is
  ignored.
- The VM accepts at most 256 KB per message from the device, so command output
  is cut at 96 KB per stream.

## Adding a command

1. Add a spec to `COMMAND_SPECS` in `executor.py`: `description`, `required`
   and `optional` parameters (each with `type` and `description`), and
   `timeout_ms` if the default 120 seconds is too short.

2. Handle it in `Executor.run`. Return `ok(payload)` or `error(message)`.

3. Child processes already run as the installing user with a clean
   environment via `self._child_options()`; there is no privilege step to
   add on macOS.

4. Add a test in `tests/test_executor.py`.

Muse sees the new command after the service restarts and re-registers.

## Say Muse, never Hatch

Users never see the name Hatch.

- Anything a person reads says Muse, the Muse app, or the Muse's name:
  - CLI output and help
  - log lines
  - errors
  - docs
- Don't use `hatch` in a new file name or identifier. Use `muse` or
  `musegadget`. The device API client is `muse_api.py`.
- `hatch` stays only where the server or the Muse app depends on it. Don't
  rename these:
  - the host `hatch.metaaivm.com`
  - the `hatch_refresh:` auth prefix
  - pairing labels and ids such as `hatch-link-pairing-v5`, the `hatch_link`
    model and the `hatch-link:` device id, and the test vectors

## Before you hand back work

1. The tests pass: `python3 -m pytest` from this directory.
2. If you changed `install.sh`, it passes ShellCheck.
3. If you deployed, the log shows `registered with the Muse` and no tracebacks.
4. New user-facing strings say Muse, never Hatch.
