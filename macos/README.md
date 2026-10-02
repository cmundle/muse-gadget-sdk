# macOS Device SDK

This turns any Mac into a Muse gadget.

Install it, pair it with the Muse app, and Muse can run commands and move
files on the machine.

Then make it your own: wire up a sensor, bridge a webhook, or give Muse a new
command.

> **Note:** Built by hackers, for hackers, just for fun. Proceed at your own
> risk! Muse gets the same access to the machine as the account you install it
> for. Unlike the Linux build, nothing here ever runs as root.

## What you need

- **A Mac with Bluetooth** (any Mac with Bluetooth LE, which is every Mac
  from the last decade).

- **macOS 13 (Ventura) or later**.

- **Python 3.9 or later** (`python3 --version` to check).

- **An SDK token** from [gadgets.muse.ai](https://gadgets.muse.ai)
  (Account > SDK tokens). Every gadget needs one to pair, including ones you
  build for yourself. Read the
  [Gadget SDK Terms](https://gadgets.muse.ai) before you use it.

- **The Muse app** on your phone, to pair the device.

## Install

On the Mac, as the account Muse should use:

```sh
curl -fsSL https://raw.githubusercontent.com/cmundle/muse-gadget-sdk/macos-port/macos/install.sh -o install.sh
less install.sh # read it first
bash install.sh --sdk-token mgst_…
```

The installer checks your system, creates a virtualenv in `/opt/musegadget`
(sudo is only used for that directory and the `/usr/local/bin` symlink),
and installs a LaunchAgent plist for the background service — but it does
**not** start the background service. Start in the terminal first (see
below); enable the LaunchAgent once pairing is stable.

## Set it up with Muse

Start the service in the foreground and open pairing:

```sh
musegadget pair
```

When the installer says pairing is open, go to the Muse app:

1. Turn on **Settings > Devices > Developer mode**.

2. Add a device (**Settings > Devices > Add Device**, the **+** icon in the
   top right). It shows up as `MuseGadgetXXXXXX`, with the name the
   installer printed.

3. Muse warns that this is a community device. Continue if it's yours.

4. When asked for Wi-Fi, pick the network shown. The machine is already
   online, so no password is needed.

That's it: run `musegadget run` in the terminal and the device connects to
Muse and stays connected until you stop it. macOS will ask for Bluetooth
permission the first time; allow it.

Pairing is open for 10 minutes. To pair again later, run `musegadget pair`.

Every setup creates a fresh encrypted session, and pairing only opens when
you run `musegadget pair` on the machine itself. Because these are
community devices, pairing has no manufacturer verification and can't prevent
an active man-in-the-middle attack. Set it up on a network you trust.

## Going to the background

Once `musegadget run` is stable in the terminal, enable the per-user
LaunchAgent so it starts at login:

```sh
launchctl load -w ~/Library/LaunchAgents/com.muse.gadget.plist
launchctl start com.muse.gadget
tail -f ~/Library/Logs/musegadget.log
```

To stop it: `launchctl unload -w ~/Library/LaunchAgents/com.muse.gadget.plist`.

## What Muse can do

| Command | What it does |
|---|---|
| `system.run` | Runs a shell command and returns its output and exit code |
| `file.read` | Reads a file, 64 KB at a time |
| `file.write` | Writes a file, 64 KB at a time, replacing it only when complete |
| `device.health` | Reports uptime, load, memory, disk and model |

Commands run as the account you installed for, with exactly that account's
permissions. The macOS service never runs as root.

Ask Muse things like:

> What's using all the disk space on my Mac?

> Every morning at 7, check if my Mac's backups ran and tell me if they didn't.

## Home theater

This Mac can also drive the home theater on your LAN: Apple TV, HomePod mini
(via pyatv), and Sonos speakers (via SoCo). These are optional: install them
with:

```sh
pip install "musegadget[hometheater]"
```

or `pip install pyatv soco` directly. Without them, the commands below fail
with a clear message telling you what to install.

| Command | What it does |
|---|---|
| `appletv.remote_key` | Press a remote key: menu, select, play_pause, up/down/left/right, home, next, previous, wakeup, suspend |
| `appletv.app_list` | List installed apps (name and identifier) |
| `appletv.launch_app` | Launch an app by name or identifier |
| `appletv.text_entry` | Type into the focused text field (fails cleanly when nothing is focused) |
| `appletv.power` | Sleep or wake the Apple TV |
| `homepod.volume` | Get or set volume (0-100); reads back afterwards |
| `homepod.transport` | play, pause, next, previous on the existing session |
| `homepod.group` | Join, unjoin, or set the output speaker group |
| `sonos.status` | Transport state, current track, volume/mute, group members |
| `sonos.volume` / `sonos.mute` | Volume and mute per room |
| `sonos.transport` | play, pause, stop, next, previous, seek on the group coordinator |
| `sonos.group` | Join or unjoin rooms |

Every command takes an optional `target`: a hostname, IP, or device name for
Apple TV / HomePod, and a room name for Sonos. When omitted, the gadget uses
the paired device, or the only device it finds. (A future `devices.json` in
the state dir may map friendly names to addresses; for now, pass explicit
values.)

### Pairing Apple TV / HomePod

pyatv needs pairing credentials for the Companion (and optionally AirPlay)
protocol. Pair once from a terminal on this Mac:

```sh
musegadget appletv-pair --target <hostname-or-IP>
```

Enter the PIN shown on the TV when asked. Credentials are saved to
`home_theater_credentials.json` in the state dir (owner-only, 0600), keyed by
the target you paired with. Re-run for each protocol you need.

### Limits

These follow the community device skills' constraints:

- HomePod transport only controls an *existing* session. It cannot start a
  new audio stream, and `play_url` is never offered for HomePod.
- No `play_url` on tvOS either, no screenshots, no account switching.
- No Siri/Intercom and no Home configuration access.
- Sonos grouping resolves each room's group coordinator from zone topology
  before acting, and state is polled after every action. Group changes affect
  other rooms. Bonded stereo or home-theater pairs should be left grouped as
  they are.

## Hack and extend it

Programs on the machine can send messages to Muse, with no credentials of
their own:

```sh
musegadget send-user-msg "The build finished."
musegadget send-user-msg --session-id 6f1c2d4e-0b7a-4c3e-9f5d-2a8b1e0c7d93 "Posted to a side chat"
```

`--session-id` posts into a side chat: a new id starts one, and reusing it
keeps later messages there.

A few other ways to build on it:

- **Let Muse do it.** Muse can run commands on the machine, so you can ask it
  to set up the rest: "Write a LaunchAgent that tells me when this Mac's disk
  is almost full."

- **Add a command.** Commands live in `src/musegadget/executor.py`:
  add a spec to `COMMAND_SPECS` and a branch in `Executor.run`.
  `AGENTS.md` walks through it.

- **Change the SDK token.** `bash install.sh --sdk-token mgst_…` replaces it.
  It's saved in `~/Library/Application Support/musegadget/sdk_token`,
  readable only by you.

## Manage it

```sh
musegadget info          # node id, BLE name and pairing state
musegadget run           # foreground service (terminal-first workflow)
musegadget pair          # pair again (10-minute window)
musegadget unpair        # forget the pairing
tail -f ~/Library/Logs/musegadget.log   # background service log

launchctl load -w ~/Library/LaunchAgents/com.muse.gadget.plist    # enable background
launchctl unload -w ~/Library/LaunchAgents/com.muse.gadget.plist  # disable background

bash install.sh --uninstall          # remove it (keeps identity and pairing)
bash install.sh --uninstall --purge  # remove it and forget the pairing
```

## Develop

From this directory:

```sh
python3 -m pytest
```

The tests run anywhere, with no Bluetooth or Muse needed. Tests touching
CoreBluetooth are skipped off macOS. To try a change on a Mac, copy this
directory to it and run `bash install.sh --from .`.

## Community

Meet other hackers who are building and customizing Muse gadgets in our
community [Discord](https://discord.gg). Get inspired, support each
other, and share what you make.

## License

Apache 2.0. See `LICENSE`.
