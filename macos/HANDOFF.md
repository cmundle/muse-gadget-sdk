# Handoff: macOS Device SDK port

**Date:** 2026-10-07
**Branch:** `macos-port` on `github.com/cmundle/muse-gadget-sdk` (fork of
`facebookincubator/muse-gadget-sdk`)
**Head:** `4b5d3dca` — "Port upstream: request text replies in send_chat"
**Tests:** 200 passing, 1 skipped when run as root (`python3 -m pytest` from `macos/`)

This is the working state for a new agent picking up the project. Read
`AGENTS.md` first for architecture and conventions, then this file for
where things stand.

## What this is

A macOS port of the Linux Muse gadget Device SDK. It turns a Mac into a
Muse Home Link: pairs with the Muse phone app over BLE (CoreBluetooth),
holds an encrypted Noise session to the user's Muse VM, and runs commands
the Muse sends it. Terminal-first workflow (`musegadget pair`, `musegadget
run`); a LaunchAgent plist ships but is not auto-loaded until the
foreground flow is stable.

## What is done and verified

- Full scaffold: `macos/` mirrors `linux/`, 38+ files. CoreBluetooth
  peripheral, macOS config paths (`~/Library/Application Support/musegadget`),
  LaunchAgent, `platform: "macos"` / `device_family: "homehub"`.
- Upstream sync: commit `1bf41be` (bound wait after `system.run` timeout)
  ported with its test; commit `b9008abb` (request `output_modality:
  "text"` in `send_chat`, drop server TTS fetch) ported to
  `macos/` with its test update. On 2026-10-05, upstream Linux fixes
  up to `74a5e2d` were ported: `f3cf874` (backoff overflow, setup
  deadlock, PermissionError handling, output clipping, non-UTF-8 control
  messages; its dbus.Boolean fix is Linux-only), `6e3fef8` (log how each
  invoke ends) and the nmcli part of `719210b`. `be99e1b` (wpa_supplicant
  fallback), `f5da932` (Linux install.sh) and `2966a7d` (Linux example
  bridge) don't apply. Everything else upstream through `74a5e2d` is
  ESP32-only or docs. On 2026-10-07, `6c33c12` (system.run waits for the
  shell, not pipe EOF) and `b139b45` (deadline on its pipe reads) were
  ported; the rest through `b139b45` is ESP32-only (Waveshare LCD7 board,
  licence headers).
- Home-theater commands (13): `appletv.*` (remote_key, app_list,
  launch_app, text_entry, power), `homepod.*` (volume, transport, group),
  `sonos.*` (status, volume, mute, transport, group). pyatv and SoCo are
  lazy optional deps (`pip install -e ".[hometheater]"`). `musegadget
  appletv-pair` CLI helper does PIN pairing and stores credentials 0600.
- BackOff retry (commit `befc2c3b`): `appletv-pair` honors the Apple TV's
  `Error=BackOff` throttle, waits it out, retries with a fresh handler
  (5 attempts). Non-throttle errors fail fast. 3 tests.
- pyatv discovery verified against real hardware (2026-10-02): found Apple
  TVs, HomePods, and Macs on a LAN. Sonos discovery not yet hardware-tested
  (no Sonos on that network).
- Install URLs point at this fork (`cmundle/muse-gadget-sdk@macos-port`),
  not upstream, because upstream has no `macos/` directory.

## Open investigation 1: Muse app does not discover the BLE peripheral

**Status:** Blocked, root cause unknown. Likely needs an upstream issue with Meta.

- `musegadget pair` on a MacBook Pro advertises correctly: Bluetooth
  powered on, GATT service registered, advertising as `MuseGadgetXXXXXX`.
- nRF Connect on iPhone **sees** the peripheral and the service UUID
  `7fdd3d1c-38ea-46cf-8b46-314ecf5f240c`. The Mac side works.
- The Muse app (Developer mode on, Bluetooth permission granted,
  force-quit and reopened, Bluetooth toggled on both devices) shows **no
  devices** on the Add Device screen. Pairing never completes.
- A manufacturer-data advertisement variant (Linux parity, paired-flag
  `0xFFFF`) was tried and **reverted**: after adding it, nRF Connect
  stopped seeing the advertisement entirely. Hypothesis: 128-bit service
  UUID + 17-char local name + manufacturer data exceeds practical BLE
  advertisement capacity on macOS. The known-good version is the
  pre-manufacturer-data one (current head).
- Full notes: `macos/docs/ble-pairing-debug.md`.
- Next step: reconfirm nRF visibility on current head with a fresh
  process, then file an upstream issue (do not file without the owner's
  explicit approval).

Environment notes for retesting: activate the venv first
(`source .venv/bin/activate` in `macos/`), and re-export
`MUSEGADGET_SDK_TOKEN` in each new shell (it is shell-local). Pairing
credentials are per machine.

## Resolved: Apple TV Companion pairing "timeout"

**Status:** Resolved 2026-10-03. Not a tvOS bug.

- `192.168.68.84` is a HomePod ("Bedroom (2)", `AudioAccessory1,1`), not
  the Apple TV. Its scan lists Companion as `Pairing: Unsupported`, so it
  never answers the first pairing message. HomePods need no pairing (RAOP
  `NotNeeded`); use `.84` for `homepod.*` commands only.
- The bedroom Apple TV is `192.168.68.62` ("Bedroom", Apple TV 4K gen 2,
  `AppleTV11,1`, tvOS 27.0, Companion `Pairing: Mandatory`). Companion
  pairing succeeded there on 2026-10-03 and the credentials are saved
  (after fixing an `IPv4Address` JSON crash in `_save_credential`).
- `appletv-pair` now checks the scan before pairing and stops with a clear
  message when the target doesn't offer or can't pair over the protocol.
  Ctrl-C, end of input or an empty PIN at the prompt cancel cleanly (exit
  130, no traceback) and close the pairing session.
- Known tvOS 27 quirk still applies (pyatv issues #2866, #2894): use
  Companion; AirPlay PIN pairing shows no PIN on tvOS 27.

## Constraints (do not violate)

- No root at runtime, ever. Everything runs as the installing user.
- User-facing strings say "Muse", never "Hatch". Protocol-required Hatch
  identifiers (`hatch.metaaivm.com`, `hatch-link-pairing-v5`, etc.) stay.
- Register as `platform: "macos"`, `device_family: "homehub"`. Never
  `link`, never advertise `device.ota`.
- Home-theater limits: no `play_url` on tvOS; HomePod controls existing
  sessions only (never starts streams); no screenshots, account
  switching, Siri/Intercom; Sonos resolves the group coordinator first.
- Follow the repo's README, Code of Conduct, and CONTRIBUTING: branch from
  `main`, tests for new code, docs updated, suite green.
- Upstream contribution is parked until the owner finishes personal
  testing. A Meta CLA will be needed before any upstream PR.
- The owner drives hardware testing himself (BLE, PIN entry). Give exact
  commands; do not attempt device interaction from here.

## Useful commands

```sh
cd ~/GitHub/muse-gadget-sdk/macos
source .venv/bin/activate
pip install -e ".[hometheater]"   # pyatv + soco, optional
python3 -m pytest                  # full suite, no hardware needed
musegadget appletv-pair --target 192.168.68.62
```

## Suggested next steps (in order)

1. Apple TV is paired (.62). `appletv.launch_app` (YouTube) verified on
   the real TV 2026-10-03. Still to confirm: `app_list`, `remote_key`,
   `text_entry`, `power`.
2. BLE: test whether the Muse app needs manufacturer data. CoreBluetooth
   can only advertise a local name and service UUIDs (Apple's
   `startAdvertising` docs), while Linux and ESP32 send company `0xFFFF`
   plus a paired flag. Control test: an Android phone running nRF Connect's
   Advertiser with the service UUID and name, once without and once with
   manufacturer data `0xFFFF` / `00`. Also check whether nRF shows the
   full `MuseGadgetXXXXXX` name (CoreBluetooth allows only 10 bytes for
   the name in the scan response). The owner has no Android device, so
   this control test is not possible for now; an upstream issue draft
   (posted in the project thread 2026-10-03) asks Meta to confirm the
   app's scan filter instead. File only with the owner's approval.
3. Sonos: hardware-test `sonos.*` commands where Sonos speakers are on the
   LAN (they were not on the test network).
4. When `pair` + `run` are stable in the foreground: load the LaunchAgent
   and verify log output.
5. ShellCheck `macos/install.sh` (never run).
