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

"""Home theater control: Apple TV and HomePod mini via pyatv, Sonos via SoCo.

Shared implementation behind the ``appletv.*``, ``homepod.*`` and ``sonos.*``
executor commands. pyatv and SoCo are optional dependencies: they are imported
lazily so the gadget runs fine without them, and every entry point degrades
to a clear error telling the user how to install the missing library.

Apple TV / HomePod pairing credentials (Companion and AirPlay PIN pairing)
are stored in the state dir as ``home_theater_credentials.json`` (0600,
atomic write, same pattern as the SDK token). Pair from a terminal with::

    musegadget appletv-pair --target <hostname-or-IP>

and enter the on-screen PIN when asked. Credentials are keyed by the target
string used at pairing time.

Skill limits respected here (see the upstream community device skills):
- no ``play_url`` on tvOS, and never for HomePod;
- HomePod transport only controls an *existing* session; it never starts a
  new audio stream;
- no screenshots, account switching, Siri/Intercom, or Home configuration;
- Sonos actions resolve the room's group coordinator from zone topology
  first, and state is polled (never pushed) after each action.
"""

from __future__ import annotations

import asyncio
import logging
import re

from musegadget import config

log = logging.getLogger(__name__)

_CREDENTIALS_FILE = "home_theater_credentials.json"
_SCAN_TIMEOUT_S = 8
_PAIR_BEGIN_ATTEMPTS = 5
_BACKOFF_RE = re.compile(r"BackOff=(\d+)\s*s", re.IGNORECASE)


class HomeTheaterError(Exception):
    """Base class for clean, user-facing home theater failures."""


class MissingDependency(HomeTheaterError):
    """A required optional library is not installed."""


class DeviceNotFound(HomeTheaterError):
    """No matching device found on the local network."""


# -- Optional dependencies ---------------------------------------------------

def _require_pyatv():
    """Import pyatv lazily; raise a clean error when it is missing."""
    try:
        import pyatv
    except ImportError:
        raise MissingDependency("pyatv is not installed; run: pip install pyatv")
    return pyatv


def _require_soco():
    """Import SoCo lazily; raise a clean error when it is missing."""
    try:
        import soco
    except ImportError:
        raise MissingDependency("SoCo is not installed; run: pip install soco")
    return soco


# -- Pairing credential storage -----------------------------------------------

def _load_credentials() -> dict:
    return config.load_json(_CREDENTIALS_FILE) or {}


def _save_credential(target: str, protocol: str, credentials: str,
                     address: str | None = None) -> None:
    """Save pyatv pairing credentials for a target (0600, atomic write)."""
    creds = _load_credentials()
    entry = creds.get(target, {})
    entry[protocol] = credentials
    if address:
        entry["address"] = address
    creds[target] = entry
    config.save_json(_CREDENTIALS_FILE, creds)


def _credentials_for(target: str | None, discovered_name: str | None) -> dict:
    saved = _load_credentials()
    for key in (target, discovered_name):
        if key and key in saved:
            return saved[key]
    return {}


# -- pyatv plumbing ------------------------------------------------------------

def _looks_like_address(target: str) -> bool:
    return "." in target or ":" in target


def _run(coro):
    """Run an async pyatv operation from synchronous code."""
    return asyncio.run(coro)


async def _find_atv_config(pyatv, target: str | None):
    """Scan the LAN and return the matching Apple TV / HomePod config."""
    loop = asyncio.get_running_loop()
    hosts = [target] if target and _looks_like_address(target) else None
    configs = await pyatv.scan(loop, hosts=hosts, timeout=_SCAN_TIMEOUT_S)
    if target and not hosts:
        needle = target.lower()
        configs = [c for c in configs
                   if needle in (c.name or "").lower()
                   or needle in (c.identifier or "").lower()]
    if not configs:
        where = f" matching {target!r}" if target else ""
        raise DeviceNotFound(
            f"no Apple TV or HomePod found{where} on this network")
    if len(configs) > 1 and not target:
        names = ", ".join(c.name or "?" for c in configs)
        raise DeviceNotFound(
            f"multiple devices found ({names}); specify target to choose one")
    return configs[0]


async def _connect_atv(pyatv, target: str | None):
    """Connect to an Apple TV / HomePod, applying saved pairing credentials."""
    cfg = await _find_atv_config(pyatv, target)
    for proto_name, cred in _credentials_for(target, cfg.name).items():
        if proto_name == "address":
            continue
        protocol = getattr(pyatv.const.Protocol, proto_name, None)
        if protocol is None:
            log.warning("ignoring credentials for unknown protocol %s", proto_name)
            continue
        cfg.set_credentials(protocol, cred)
    loop = asyncio.get_running_loop()
    try:
        atv = await pyatv.connect(cfg, loop)
    except Exception as exc:
        raise HomeTheaterError(
            f"could not connect to {cfg.name or target}: {exc}; "
            "if pairing is required, run "
            "`musegadget appletv-pair --target <hostname-or-IP>` first")
    return atv, cfg


def _with_atv(target: str | None, op):
    """Connect, run async ``op(atv, config)``, close, return its result."""
    pyatv = _require_pyatv()

    async def _main():
        atv, cfg = await _connect_atv(pyatv, target)
        try:
            return await op(atv, cfg)
        finally:
            atv.close()

    return _run(_main())


def _device_summary(cfg) -> dict:
    info = cfg.device_info
    return {
        "name": cfg.name,
        "address": cfg.address,
        "model": getattr(info, "raw_model", None),
        "os": getattr(info, "operating_system", None),
        "os_version": getattr(info, "version", None),
    }


# -- Apple TV commands ----------------------------------------------------------

_REMOTE_KEYS = {
    "menu": "menu",
    "select": "select",
    "play_pause": "play_pause",
    "play": "play",
    "pause": "pause",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "home": "home",
    "next": "next",
    "previous": "previous",
    "wakeup": "wakeup",
    "suspend": "suspend",
}


def appletv_remote_key(params: dict) -> dict:
    key = params.get("key", "")
    method = _REMOTE_KEYS.get(key)
    if method is None:
        raise HomeTheaterError(
            f"unknown remote key {key!r}; use one of: {', '.join(sorted(_REMOTE_KEYS))}")

    async def _op(atv, cfg):
        await getattr(atv.remote_control, method)()
        return {
            "device": _device_summary(cfg),
            "key": key,
            "note": "key sent; success does not guarantee the on-screen outcome",
        }

    return _with_atv(params.get("target"), _op)


def appletv_app_list(params: dict) -> dict:
    async def _op(atv, cfg):
        apps = await atv.apps.app_list()
        return {
            "device": _device_summary(cfg),
            "apps": [{"name": a.name, "identifier": a.identifier} for a in apps],
        }

    return _with_atv(params.get("target"), _op)


def appletv_launch_app(params: dict) -> dict:
    want = (params.get("app") or "").strip()
    if not want:
        raise HomeTheaterError("app is required")

    async def _op(atv, cfg):
        apps = await atv.apps.app_list()
        needle = want.lower()
        match = next(
            (a for a in apps
             if a.identifier.lower() == needle or a.name.lower() == needle
             or needle in a.name.lower()),
            None,
        )
        if match is None:
            raise DeviceNotFound(
                f"app {want!r} is not installed on {cfg.name or 'this Apple TV'}; "
                "use appletv.app_list to see installed apps")
        await atv.apps.launch_app(match.identifier)
        return {
            "device": _device_summary(cfg),
            "app": match.name,
            "identifier": match.identifier,
        }

    return _with_atv(params.get("target"), _op)


def appletv_text_entry(params: dict) -> dict:
    text = params.get("text", "")
    if not text:
        raise HomeTheaterError("text is required")

    async def _op(atv, cfg):
        pyatv = _require_pyatv()
        try:
            state = atv.keyboard.text_focus_state
        except Exception:
            state = None
        focused = (pyatv.const.KeyboardFocusState.Focused,)
        if state is not None and state not in focused:
            raise HomeTheaterError(
                "no text field is focused on the Apple TV; "
                "focus a text field first, then try again")
        await atv.keyboard.text_set(text)
        result = {
            "device": _device_summary(cfg),
            "typed": text,
        }
        if state is None:
            result["note"] = "text focus could not be confirmed on this device"
        return result

    return _with_atv(params.get("target"), _op)


def appletv_power(params: dict) -> dict:
    action = params.get("action", "")
    if action not in ("sleep", "wake"):
        raise HomeTheaterError("action must be 'sleep' or 'wake'")

    async def _op(atv, cfg):
        if action == "wake":
            await atv.power.turn_on()
            note = "wake sent; may also wake a connected display over HDMI-CEC"
        else:
            await atv.power.turn_off()
            note = "sleep sent"
        return {"device": _device_summary(cfg), "action": action, "note": note}

    return _with_atv(params.get("target"), _op)


# -- HomePod commands ------------------------------------------------------------

async def _homepod_volume_value(atv) -> float:
    volume = atv.audio.volume
    if asyncio.iscoroutine(volume):
        volume = await volume
    return float(volume)


def homepod_volume(params: dict) -> dict:
    level = params.get("level")
    step = params.get("step")

    async def _op(atv, cfg):
        if level is None and step is None:
            return {
                "device": _device_summary(cfg),
                "volume": await _homepod_volume_value(atv),
            }
        if level is not None:
            want = max(0, min(100, int(level)))
            await atv.audio.set_volume(float(want))
        else:
            for _ in range(abs(int(step))):
                if int(step) > 0:
                    await atv.audio.volume_up()
                else:
                    await atv.audio.volume_down()
        actual = await _homepod_volume_value(atv)
        result: dict = {"device": _device_summary(cfg), "volume": actual}
        if level is not None and actual != float(want):
            # Some firmware acknowledges the setter without changing the level.
            result["note"] = (
                f"requested {want} but the device reports {actual}; "
                "the read-back value is authoritative")
        return result

    return _with_atv(params.get("target"), _op)


async def _existing_session(atv):
    """Return the current playback metadata, or None when idle.

    Handles both property-style and coroutine-style ``metadata.playing``
    across pyatv versions.
    """
    try:
        playing = atv.metadata.playing
    except Exception:
        return None
    if callable(playing):
        try:
            playing = await playing()
        except Exception:
            return None
    return playing


def homepod_transport(params: dict) -> dict:
    action = params.get("action", "")
    if action not in ("play", "pause", "next", "previous"):
        raise HomeTheaterError("action must be play, pause, next, or previous")

    async def _op(atv, cfg):
        if await _existing_session(atv) is None:
            raise HomeTheaterError(
                "no active playback session on this HomePod; "
                "this command cannot start a new audio stream")
        await getattr(atv.remote_control, action)()
        return {"device": _device_summary(cfg), "action": action}

    return _with_atv(params.get("target"), _op)


def homepod_group(params: dict) -> dict:
    action = params.get("action", "")
    speakers = params.get("speakers") or []
    if action not in ("join", "unjoin", "set"):
        raise HomeTheaterError("action must be join, unjoin, or set")
    if not speakers:
        raise HomeTheaterError("speakers is required")

    async def _op(atv, cfg):
        # Receiver IDs are the speaker names as advertised; group changes
        # affect other rooms.
        if action == "join":
            await atv.audio.add_output_devices(*speakers)
        elif action == "unjoin":
            await atv.audio.remove_output_devices(*speakers)
        else:
            await atv.audio.set_output_devices(*speakers)
        current = [
            {"name": d.name, "volume": d.volume}
            for d in atv.audio.output_devices
        ]
        return {
            "device": _device_summary(cfg),
            "action": action,
            "speakers": speakers,
            "group": current,
        }

    return _with_atv(params.get("target"), _op)


# -- Sonos commands ---------------------------------------------------------------

def _soco_speakers() -> list:
    soco = _require_soco()
    found = soco.discover(timeout=5)
    if not found:
        raise DeviceNotFound("no Sonos speakers found on this network")
    return list(found)


def _find_room(room: str):
    needle = room.lower()
    for speaker in _soco_speakers():
        if (speaker.player_name or "").lower() == needle:
            return speaker
    raise DeviceNotFound(f"no Sonos room named {room!r} found on this network")


def _coordinator(speaker):
    """Resolve the room's group coordinator from zone topology."""
    group = speaker.group
    if group is not None and getattr(group, "coordinator", None) is not None:
        return group.coordinator
    return speaker


def _sonos_state(speaker) -> dict:
    coord = _coordinator(speaker)
    transport = coord.get_current_transport_info() or {}
    track = coord.get_current_track_info() or {}
    group = coord.group
    members = ([m.player_name for m in group.members]
               if group is not None else [coord.player_name])
    return {
        "room": speaker.player_name,
        "coordinator": coord.player_name,
        "transport_state": transport.get("current_transport_state"),
        "track": {k: track.get(k) for k in ("artist", "album", "title")},
        "volume": speaker.volume,
        "muted": speaker.mute,
        "group_members": members,
    }


def sonos_status(params: dict) -> dict:
    room = params.get("room", "")
    if not room:
        raise HomeTheaterError("room is required")
    return _sonos_state(_find_room(room))


def sonos_volume(params: dict) -> dict:
    room = params.get("room", "")
    if not room:
        raise HomeTheaterError("room is required")
    level = params.get("level")
    step = params.get("step")
    speaker = _find_room(room)
    if level is not None:
        speaker.volume = max(0, min(100, int(level)))
    elif step is not None:
        speaker.volume = max(0, min(100, speaker.volume + int(step)))
    return {"room": speaker.player_name, "volume": speaker.volume}


def sonos_mute(params: dict) -> dict:
    room = params.get("room", "")
    if not room:
        raise HomeTheaterError("room is required")
    speaker = _find_room(room)
    speaker.mute = bool(params.get("muted"))
    return {"room": speaker.player_name, "muted": speaker.mute}


_SONOS_TRANSPORT_ACTIONS = ("play", "pause", "stop", "next", "previous", "seek")


def _seek_position(value: str) -> str:
    """Normalize a seek position (seconds or HH:MM:SS) for SoCo."""
    text = str(value).strip()
    if ":" in text:
        return text
    total = int(float(text))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


def sonos_transport(params: dict) -> dict:
    room = params.get("room", "")
    action = params.get("action", "")
    if not room:
        raise HomeTheaterError("room is required")
    if action not in _SONOS_TRANSPORT_ACTIONS:
        raise HomeTheaterError(
            f"unknown action {action!r}; use one of: {', '.join(_SONOS_TRANSPORT_ACTIONS)}")
    speaker = _find_room(room)
    coord = _coordinator(speaker)
    if action == "seek":
        coord.seek(_seek_position(params.get("position", "0")))
    else:
        getattr(coord, action)()
    # Poll state after the action; the device never pushes to us.
    info = coord.get_current_transport_info() or {}
    return {
        "room": speaker.player_name,
        "coordinator": coord.player_name,
        "action": action,
        "transport_state": info.get("current_transport_state"),
    }


def sonos_group(params: dict) -> dict:
    action = params.get("action", "")
    rooms = params.get("rooms") or []
    if action not in ("join", "unjoin"):
        raise HomeTheaterError("action must be join or unjoin")
    if not rooms:
        raise HomeTheaterError("rooms is required")
    if action == "join":
        anchor = params.get("group_with") or rooms[0]
        master = _coordinator(_find_room(anchor))
        for room in rooms:
            if room.lower() == anchor.lower():
                continue
            _find_room(room).join(master)
        # Re-resolve: joining can move the coordinator.
        final = _coordinator(master)
        group = final.group
        members = ([m.player_name for m in group.members]
                   if group is not None else [final.player_name])
        return {"action": "join", "anchor": anchor, "group": members}
    for room in rooms:
        _find_room(room).unjoin()
    return {"action": "unjoin", "rooms": rooms}


# -- Pairing ---------------------------------------------------------------------

async def _begin_pairing_with_backoff(pyatv, config, protocol, loop,
                                      attempts: int = _PAIR_BEGIN_ATTEMPTS):
    """Create a pairing handler and begin it, honoring BackOff throttles.

    Apple TVs answer ``begin()`` with ``Error=BackOff, BackOff=Ns`` when
    pairing attempts arrive too fast (e.g. a previous attempt was abandoned
    mid-handshake). Wait out the requested backoff and retry with a fresh
    handler instead of failing with a traceback.
    """
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        pairing = await pyatv.pair(config, protocol, loop)
        try:
            await pairing.begin()
            return pairing
        except Exception as exc:
            last_error = exc
            try:
                await pairing.close()
            except Exception:
                log.debug("error closing throttled pairing handler",
                          exc_info=True)
            backoff = _BACKOFF_RE.search(str(exc))
            if backoff is None or attempt == attempts:
                raise
            wait_s = int(backoff.group(1)) + 1  # small margin on the ask
            log.info("pairing throttled, retrying in %ds (attempt %d/%d)",
                     wait_s, attempt, attempts)
            print(f"Device asked us to wait {wait_s}s before retrying "
                  f"(attempt {attempt}/{attempts})...")
            await asyncio.sleep(wait_s)
    raise last_error  # unreachable: the loop always returns or raises


def pair_apple_tv(target: str, protocol: str = "Companion") -> dict:
    """Pair with an Apple TV or HomePod over the LAN (interactive).

    Scans for the target, starts PIN pairing for the given protocol, reads
    the PIN from stdin, and saves the resulting credentials to the state dir.
    Run this in a terminal, not through a Muse command.
    """
    pyatv = _require_pyatv()
    protocol_obj = getattr(pyatv.const.Protocol, protocol, None)
    if protocol_obj is None:
        raise HomeTheaterError(f"unknown protocol {protocol!r}")

    async def _main():
        loop = asyncio.get_running_loop()
        hosts = [target] if _looks_like_address(target) else None
        configs = await pyatv.scan(loop, hosts=hosts, timeout=_SCAN_TIMEOUT_S)
        if target and not hosts:
            needle = target.lower()
            configs = [c for c in configs
                       if needle in (c.name or "").lower()
                       or needle in (c.identifier or "").lower()]
        if not configs:
            raise DeviceNotFound(
                f"no Apple TV or HomePod found matching {target!r}")
        cfg = configs[0]
        pairing = await _begin_pairing_with_backoff(
            pyatv, cfg, protocol_obj, loop)
        try:
            pin = input("Enter the PIN shown on the device: ").strip()
            pairing.pin(pin)
            await pairing.finish()
            if not pairing.has_paired:
                raise HomeTheaterError("pairing failed: the PIN was not accepted")
            _save_credential(target, protocol, pairing.service.credentials,
                             address=cfg.address)
            return {
                "paired": True,
                "device": cfg.name,
                "protocol": protocol,
            }
        finally:
            await pairing.close()

    return _run(_main())


# -- Dispatch ----------------------------------------------------------------------

_APPLETV_COMMANDS = {
    "remote_key": appletv_remote_key,
    "app_list": appletv_app_list,
    "launch_app": appletv_launch_app,
    "text_entry": appletv_text_entry,
    "power": appletv_power,
}

_HOMEPOD_COMMANDS = {
    "volume": homepod_volume,
    "transport": homepod_transport,
    "group": homepod_group,
}

_SONOS_COMMANDS = {
    "status": sonos_status,
    "volume": sonos_volume,
    "mute": sonos_mute,
    "transport": sonos_transport,
    "group": sonos_group,
}


def run_appletv(subcommand: str, params: dict) -> dict:
    try:
        handler = _APPLETV_COMMANDS[subcommand]
    except KeyError:
        raise HomeTheaterError(f"unsupported appletv command: {subcommand}")
    return handler(params)


def run_homepod(subcommand: str, params: dict) -> dict:
    try:
        handler = _HOMEPOD_COMMANDS[subcommand]
    except KeyError:
        raise HomeTheaterError(f"unsupported homepod command: {subcommand}")
    return handler(params)


def run_sonos(subcommand: str, params: dict) -> dict:
    try:
        handler = _SONOS_COMMANDS[subcommand]
    except KeyError:
        raise HomeTheaterError(f"unsupported sonos command: {subcommand}")
    return handler(params)
