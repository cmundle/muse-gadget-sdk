# Handoff: macOS Device SDK port

**Date:** 2026-10-03
**Branch:** `macos-port` on `github.com/cmundle/muse-gadget-sdk` (fork of
`facebookincubator/muse-gadget-sdk`)
**Head:** `4b5d3dca` — "Port upstream: request text replies in send_chat"
**Tests:** 174 passing (`python3 -m pytest` from `macos/`)

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
  `macos/` with its test update. Other upstream commits since
  `1bf41be` are ESP32-only (board support, camera, simulator).
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

## Open investigation 2: Apple TV Companion pairing times out on tvOS 27

**Status:** In progress. Blocked on the device side, not the code.

- Target: bedroom Apple TV at `192.168.68.84` (tvOS 27), with HomePods as
  its audio output. (Sam's Apple TV at `.53` is not accessible to the owner;
  use `.84`.)
- First attempts got `Error=BackOff` from the TV; the retry fix
  (`befc2c3b`) resolved that.
- Current failure: `pyatv.exceptions.ConnectionFailedError` caused by
  `asyncio.TimeoutError` in `exchange_auth` — the TV does not answer the
  first Companion pairing message. The TV is confirmed awake and on the
  home screen.
- Ruled out: pyatv version (0.18.0 installed, current, confirmed working
  with tvOS 27 elsewhere), TV asleep.
- Known tvOS 27 quirks (pyatv issues #2866, #2894): Companion pairing is
  the working path; AirPlay PIN pairing is broken on tvOS 27 (no PIN
  shown). Do not switch to `--protocol AirPlay` expecting it to work.
- Hypotheses still open: the TV's pairing service is wedged from the
  earlier throttled attempts (try Settings → System → Restart); the TV's
  Settings → Remotes and Devices → Remote App and Devices may not allow
  new pairings; or tvOS 27 changed something in the handshake pyatv
  0.18.0 does not handle.
- Control test: pair the owner's iPhone as a remote to that TV. If the
  iPhone fails too, it is the TV. If the iPhone works, dig into pyatv's
  Companion handshake.

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
musegadget appletv-pair --target 192.168.68.84
```

## Suggested next steps (in order)

1. Apple TV pairing: restart the bedroom Apple TV, retry `appletv-pair`.
   If still wedged, run the iPhone control test, then investigate pyatv's
   Companion handshake against tvOS 27.
2. Once Apple TV pairs: exercise `appletv.remote_key`, `app_list`,
   `launch_app` against the real TV.
3. BLE: reconfirm nRF visibility on current head, then draft the upstream
   issue for the Muse app discovery failure.
4. Sonos: hardware-test `sonos.*` commands where Sonos speakers are on the
   LAN (they were not on the test network).
5. When `pair` + `run` are stable in the foreground: load the LaunchAgent
   and verify log output.
6. ShellCheck `macos/install.sh` (never run).
