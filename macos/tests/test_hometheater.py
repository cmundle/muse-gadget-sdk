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

"""Tests for home theater commands (Apple TV / HomePod via pyatv, Sonos via SoCo).

pyatv and SoCo are optional dependencies, so these tests inject lightweight
fakes into sys.modules instead of requiring the real libraries.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from musegadget import executor, hometheater
from musegadget.executor import Account, Executor, COMMAND_SPECS

NEW_COMMANDS = [name for name in COMMAND_SPECS if name.split(".")[0] in
                ("appletv", "homepod", "sonos")]


# -- Fake pyatv ---------------------------------------------------------------

def _make_fake_pyatv():
    mod = types.ModuleType("pyatv")
    const = types.ModuleType("pyatv.const")

    class Protocol:
        Companion = "Companion"
        AirPlay = "AirPlay"

    class KeyboardFocusState:
        Unknown = 0
        Unfocused = 1
        Focused = 2

    const.Protocol = Protocol
    const.KeyboardFocusState = KeyboardFocusState
    mod.const = const

    class FakeDeviceInfo:
        raw_model = "AppleTV14,1"
        model = "Apple TV 4K"
        operating_system = "tvOS"
        version = "26.1"

    class FakeConfig:
        def __init__(self, name="Living Room TV", address="10.0.0.10",
                     identifier="AA:BB:CC"):
            self.name = name
            self.address = address
            self.identifier = identifier
            self.credentials = {}
            self.device_info = FakeDeviceInfo()

        def set_credentials(self, protocol, credentials):
            self.credentials[protocol] = credentials

    class FakeRemote:
        def __init__(self):
            self.pressed = []

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)

            async def _press(*args, **kwargs):
                self.pressed.append(name)

            return _press

    class FakeApp:
        def __init__(self, name, identifier):
            self.name = name
            self.identifier = identifier

    class FakeApps:
        def __init__(self, apps):
            self._apps = apps
            self.launched = []

        async def app_list(self):
            return list(self._apps)

        async def launch_app(self, identifier):
            self.launched.append(identifier)

    class FakeKeyboard:
        def __init__(self, focused=True):
            self._focused = focused
            self.typed = []

        @property
        def text_focus_state(self):
            return (KeyboardFocusState.Focused if self._focused
                    else KeyboardFocusState.Unfocused)

        async def text_set(self, text):
            self.typed.append(text)

    class FakePower:
        def __init__(self):
            self.actions = []

        async def turn_on(self):
            self.actions.append("wake")

        async def turn_off(self):
            self.actions.append("sleep")

    class FakeAudio:
        def __init__(self):
            self._volume = 50.0
            self.set_calls = []
            self._outputs = []

        @property
        def volume(self):
            return self._volume

        async def set_volume(self, level, output_device=None):
            self.set_calls.append(level)
            self._volume = float(level)

        async def volume_up(self):
            self._volume += 1.0

        async def volume_down(self):
            self._volume -= 1.0

        @property
        def output_devices(self):
            return list(self._outputs)

        async def add_output_devices(self, *devices):
            for d in devices:
                if d not in self._outputs:
                    self._outputs.append(_FakeOutputDevice(d))

        async def remove_output_devices(self, *devices):
            self._outputs = [o for o in self._outputs if o.name not in devices]

        async def set_output_devices(self, *devices):
            self._outputs = [_FakeOutputDevice(d) for d in devices]

    class _FakeOutputDevice:
        def __init__(self, name):
            self.name = name
            self.volume = 50.0

    class FakeMetadata:
        def __init__(self, playing=None):
            self._playing = playing

        @property
        def playing(self):
            return self._playing

    class FakeAppleTV:
        last_instance = None

        def __init__(self, config):
            self._config = config
            self.remote_control = FakeRemote()
            self.apps = FakeApps([
                FakeApp("Netflix", "com.netflix.Netflix"),
                FakeApp("YouTube", "com.google.ios.youtube"),
            ])
            self.keyboard = FakeKeyboard()
            self.power = FakePower()
            self.audio = FakeAudio()
            self.metadata = FakeMetadata()
            self.device_info = config.device_info
            self.closed = False
            FakeAppleTV.last_instance = self

        def close(self):
            self.closed = True

    class FakeService:
        credentials = "fake-credentials"
        protocol = "Companion"

    class FakePairing:
        instance = None

        def __init__(self):
            self.started = False
            self.pin_value = None
            self.finished = False
            self.service = FakeService()
            FakePairing.instance = self

        @property
        def device_provides_pin(self):
            return False

        async def begin(self):
            self.started = True

        def pin(self, pin):
            self.pin_value = pin

        async def finish(self):
            self.finished = True

        @property
        def has_paired(self):
            return self.finished and self.pin_value == "1234"

        async def close(self):
            pass

    state = {"configs": []}

    async def scan(loop, hosts=None, timeout=5):
        return list(state["configs"])

    async def connect(config, loop, protocol=None, session=None, storage=None):
        return FakeAppleTV(config)

    async def pair(config, protocol, loop, session=None, storage=None, **kwargs):
        return FakePairing()

    mod.scan = scan
    mod.connect = connect
    mod.pair = pair
    mod.FakeConfig = FakeConfig
    mod.FakeAppleTV = FakeAppleTV
    mod.FakePairing = FakePairing
    mod.state = state
    return mod


# -- Fake SoCo -----------------------------------------------------------------

def _make_fake_soco():
    mod = types.ModuleType("soco")

    class FakeGroup:
        def __init__(self, coordinator, members):
            self.coordinator = coordinator
            self.members = list(members)

    class FakeSonos:
        def __init__(self, name):
            self.player_name = name
            self._volume = 20
            self._mute = False
            self._group = None
            self.calls = []

        @property
        def volume(self):
            return self._volume

        @volume.setter
        def volume(self, value):
            self._volume = int(value)

        @property
        def mute(self):
            return self._mute

        @mute.setter
        def mute(self, value):
            self._mute = bool(value)

        @property
        def group(self):
            return self._group

        def _record(self, call):
            self.calls.append(call)

        def play(self):
            self._record("play")

        def pause(self):
            self._record("pause")

        def stop(self):
            self._record("stop")

        def next(self):
            self._record("next")

        def previous(self):
            self._record("previous")

        def seek(self, position=None, track=None):
            self._record(("seek", position))

        def join(self, master):
            self._record(("join", master.player_name))
            target = master.group or FakeGroup(master, [master])
            if self._group is not None and self in self._group.members:
                self._group.members.remove(self)
            if self not in target.members:
                target.members.append(self)
            self._group = target
            master._group = target

        def unjoin(self):
            self._record("unjoin")
            if self._group is not None and self in self._group.members:
                self._group.members.remove(self)
            self._group = None

        def get_current_transport_info(self):
            return {"current_transport_state": "PLAYING"}

        def get_current_track_info(self):
            return {"artist": "Artist", "album": "Album", "title": "Title"}

    state = {"speakers": []}

    def discover(timeout=5, **kwargs):
        return set(state["speakers"]) or None

    mod.SoCo = FakeSonos
    mod.discover = discover
    mod.FakeSonos = FakeSonos
    mod.FakeGroup = FakeGroup
    mod.state = state
    return mod


@pytest.fixture
def fake_pyatv(monkeypatch):
    mod = _make_fake_pyatv()
    monkeypatch.setitem(sys.modules, "pyatv", mod)
    monkeypatch.setitem(sys.modules, "pyatv.const", mod.const)
    mod.state["configs"] = [mod.FakeConfig()]
    return mod


@pytest.fixture
def fake_soco(monkeypatch):
    mod = _make_fake_soco()
    monkeypatch.setitem(sys.modules, "soco", mod)
    return mod


@pytest.fixture
def _block_imports(monkeypatch):
    """Factory fixture: block imports of the given top-level module names."""
    import importlib.abc

    def _block(*names):
        class _Blocker(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                if fullname in names or fullname.startswith(tuple(n + "." for n in names)):
                    raise ImportError(f"No module named {fullname!r} (blocked for test)")
                return None

        monkeypatch.setattr(sys, "meta_path", [_Blocker()] + sys.meta_path)
        for mod in [m for m in list(sys.modules)
                    if m in names or m.startswith(tuple(n + "." for n in names))]:
            monkeypatch.delitem(sys.modules, mod, raising=False)

    return _block


@pytest.fixture
def no_pyatv(_block_imports):
    _block_imports("pyatv")


@pytest.fixture
def no_soco(_block_imports):
    _block_imports("soco")


@pytest.fixture
def ex():
    return Executor(Account("test", 501, 20, "/tmp"))


# -- Spec well-formedness ----------------------------------------------------------

def test_new_command_specs_are_well_formed():
    assert len(NEW_COMMANDS) == 13, f"expected 13 home theater commands, got {NEW_COMMANDS}"
    for name in NEW_COMMANDS:
        spec = COMMAND_SPECS[name]
        assert isinstance(spec["description"], str) and spec["description"], name
        for section in ("required", "optional"):
            assert isinstance(spec[section], dict), name
            for param, pdef in spec[section].items():
                assert isinstance(pdef["type"], str), (name, param)
                assert isinstance(pdef["description"], str) and pdef["description"], (name, param)


def test_new_commands_cover_the_expected_surface():
    assert {n.split(".", 1)[1] for n in NEW_COMMANDS if n.startswith("appletv.")} == {
        "remote_key", "app_list", "launch_app", "text_entry", "power"}
    assert {n.split(".", 1)[1] for n in NEW_COMMANDS if n.startswith("homepod.")} == {
        "volume", "transport", "group"}
    assert {n.split(".", 1)[1] for n in NEW_COMMANDS if n.startswith("sonos.")} == {
        "status", "volume", "mute", "transport", "group"}


# -- Missing dependencies ----------------------------------------------------------

def test_missing_pyatv_gives_clean_error(no_pyatv, ex):
    result = ex.run("appletv.remote_key", {"key": "menu"})
    assert result == {"ok": False, "error": "pyatv is not installed; run: pip install pyatv"}


def test_missing_soco_gives_clean_error(no_soco, ex):
    result = ex.run("sonos.status", {"room": "Living Room"})
    assert result == {"ok": False, "error": "SoCo is not installed; run: pip install soco"}


def test_missing_homepod_dependency_is_clean(no_pyatv, ex):
    result = ex.run("homepod.volume", {})
    assert result["ok"] is False and "pip install pyatv" in result["error"]


# -- Apple TV -----------------------------------------------------------------------

def test_appletv_remote_key_routes(fake_pyatv, ex):
    result = ex.run("appletv.remote_key", {"key": "menu"})
    assert result["ok"] is True
    assert result["payload"]["key"] == "menu"
    atv = fake_pyatv.FakeAppleTV.last_instance
    assert atv.remote_control.pressed == ["menu"]
    assert atv.closed is True  # connection is closed after the operation


def test_appletv_remote_key_rejects_unknown(fake_pyatv, ex):
    result = ex.run("appletv.remote_key", {"key": "self_destruct"})
    assert result["ok"] is False
    assert "unknown remote key" in result["error"]


def test_appletv_app_list(fake_pyatv, ex):
    result = ex.run("appletv.app_list", {})
    assert result["ok"] is True
    names = [a["name"] for a in result["payload"]["apps"]]
    assert "Netflix" in names


def test_appletv_launch_app_resolves_by_name(fake_pyatv, ex):
    result = ex.run("appletv.launch_app", {"app": "netflix"})
    assert result["ok"] is True
    assert result["payload"]["identifier"] == "com.netflix.Netflix"
    atv = fake_pyatv.FakeAppleTV.last_instance
    assert atv.apps.launched == ["com.netflix.Netflix"]


def test_appletv_launch_app_unknown_app(fake_pyatv, ex):
    result = ex.run("appletv.launch_app", {"app": "No Such App"})
    assert result["ok"] is False
    assert "not installed" in result["error"]


def test_appletv_text_entry_types_when_focused(fake_pyatv, ex):
    result = ex.run("appletv.text_entry", {"text": "hello"})
    assert result["ok"] is True
    atv = fake_pyatv.FakeAppleTV.last_instance
    assert atv.keyboard.typed == ["hello"]


def test_appletv_text_entry_requires_focus(fake_pyatv, ex, monkeypatch):
    mod = fake_pyatv

    class _UnfocusedKeyboard:
        @property
        def text_focus_state(self):
            return mod.const.KeyboardFocusState.Unfocused

        async def text_set(self, text):
            raise AssertionError("should not type when unfocused")

    class UnfocusedATV(mod.FakeAppleTV):
        def __init__(self, config):
            super().__init__(config)
            self.keyboard = _UnfocusedKeyboard()

    async def connect(config, loop, protocol=None, session=None, storage=None):
        return UnfocusedATV(config)

    monkeypatch.setattr(mod, "connect", connect)
    result = ex.run("appletv.text_entry", {"text": "hello"})
    assert result["ok"] is False
    assert "no text field is focused" in result["error"]


def test_appletv_power_actions(fake_pyatv, ex):
    assert ex.run("appletv.power", {"action": "wake"})["payload"]["action"] == "wake"
    assert fake_pyatv.FakeAppleTV.last_instance.power.actions == ["wake"]
    assert ex.run("appletv.power", {"action": "sleep"})["payload"]["action"] == "sleep"
    assert "HDMI-CEC" in ex.run("appletv.power", {"action": "wake"})["payload"]["note"]


def test_appletv_power_rejects_bad_action(fake_pyatv, ex):
    result = ex.run("appletv.power", {"action": "explode"})
    assert result["ok"] is False
    assert "sleep" in result["error"] and "wake" in result["error"]


def test_appletv_no_device_found(fake_pyatv, ex):
    fake_pyatv.state["configs"] = []
    result = ex.run("appletv.remote_key", {"key": "menu"})
    assert result["ok"] is False
    assert "no Apple TV or HomePod found" in result["error"]


# -- HomePod --------------------------------------------------------------------------

def test_homepod_volume_readback(fake_pyatv, ex):
    result = ex.run("homepod.volume", {"level": 30})
    assert result["ok"] is True
    assert result["payload"]["volume"] == 30.0
    atv = fake_pyatv.FakeAppleTV.last_instance
    assert atv.audio.set_calls == [30.0]


def test_homepod_volume_get_only(fake_pyatv, ex):
    result = ex.run("homepod.volume", {})
    assert result["ok"] is True
    assert result["payload"]["volume"] == 50.0


def test_homepod_transport_refuses_without_session(fake_pyatv, ex):
    # FakeMetadata.playing defaults to None: no existing session.
    result = ex.run("homepod.transport", {"action": "play"})
    assert result["ok"] is False
    assert "cannot start a new audio stream" in result["error"]


def test_homepod_transport_with_existing_session(fake_pyatv, ex, monkeypatch):
    mod = fake_pyatv

    class SessionATV(mod.FakeAppleTV):
        def __init__(self, config):
            super().__init__(config)
            self.metadata._playing = {"title": "Something"}

    async def connect(config, loop, protocol=None, session=None, storage=None):
        return SessionATV(config)

    monkeypatch.setattr(mod, "connect", connect)
    result = ex.run("homepod.transport", {"action": "pause"})
    assert result["ok"] is True
    assert result["payload"]["action"] == "pause"


def test_homepod_group_reports_membership(fake_pyatv, ex):
    result = ex.run("homepod.group",
                    {"action": "join", "speakers": ["Kitchen", "Bedroom"]})
    assert result["ok"] is True
    names = [d["name"] for d in result["payload"]["group"]]
    assert names == ["Kitchen", "Bedroom"]


# -- Sonos -----------------------------------------------------------------------------

def _two_room_setup(mod):
    living = mod.FakeSonos("Living Room")
    kitchen = mod.FakeSonos("Kitchen")
    group = mod.FakeGroup(living, [living, kitchen])
    living._group = group
    kitchen._group = group
    mod.state["speakers"] = [living, kitchen]
    return living, kitchen


def test_sonos_status(fake_soco, ex):
    living, kitchen = _two_room_setup(fake_soco)
    result = ex.run("sonos.status", {"room": "Kitchen"})
    assert result["ok"] is True
    payload = result["payload"]
    assert payload["coordinator"] == "Living Room"
    assert payload["group_members"] == ["Living Room", "Kitchen"]
    assert payload["transport_state"] == "PLAYING"
    assert payload["track"]["title"] == "Title"


def test_sonos_transport_acts_on_coordinator(fake_soco, ex):
    living, kitchen = _two_room_setup(fake_soco)
    result = ex.run("sonos.transport", {"room": "Kitchen", "action": "pause"})
    assert result["ok"] is True
    assert result["payload"]["coordinator"] == "Living Room"
    assert living.calls == ["pause"]
    assert kitchen.calls == []


def test_sonos_volume_and_mute(fake_soco, ex):
    living, kitchen = _two_room_setup(fake_soco)
    assert ex.run("sonos.volume", {"room": "Kitchen", "level": 33})["payload"]["volume"] == 33
    assert kitchen._volume == 33
    assert ex.run("sonos.mute", {"room": "Kitchen", "muted": True})["payload"]["muted"] is True
    assert kitchen._mute is True


def test_sonos_group_join_and_unjoin(fake_soco, ex):
    mod = fake_soco
    living = mod.FakeSonos("Living Room")
    kitchen = mod.FakeSonos("Kitchen")
    office = mod.FakeSonos("Office")
    mod.state["speakers"] = [living, kitchen, office]

    result = ex.run("sonos.group",
                    {"action": "join", "rooms": ["Kitchen", "Office"],
                     "group_with": "Living Room"})
    assert result["ok"] is True
    assert sorted(result["payload"]["group"]) == ["Kitchen", "Living Room", "Office"]

    result = ex.run("sonos.group", {"action": "unjoin", "rooms": ["Kitchen"]})
    assert result["ok"] is True
    assert kitchen._group is None


def test_sonos_unknown_room(fake_soco, ex):
    _two_room_setup(fake_soco)
    result = ex.run("sonos.status", {"room": "Garage"})
    assert result["ok"] is False
    assert "no Sonos room named 'Garage'" in result["error"]


def test_sonos_no_speakers_on_network(fake_soco, ex):
    fake_soco.state["speakers"] = []
    result = ex.run("sonos.status", {"room": "Living Room"})
    assert result["ok"] is False
    assert "no Sonos speakers found" in result["error"]


def test_sonos_seek_position_normalized(fake_soco, ex):
    living, kitchen = _two_room_setup(fake_soco)
    result = ex.run("sonos.transport",
                    {"room": "Living Room", "action": "seek", "position": "90"})
    assert result["ok"] is True
    assert living.calls == [("seek", "0:01:30")]


# -- Executor integration -----------------------------------------------------------------

def test_unknown_subcommand_is_a_clean_error(fake_pyatv, fake_soco, ex):
    result = ex.run("appletv.eject", {})
    assert result == {"ok": False, "error": "unsupported appletv command: eject"}
    result = ex.run("sonos.dance", {"room": "Living Room"})
    assert result == {"ok": False, "error": "unsupported sonos command: dance"}


def test_seek_position_normalization():
    assert hometheater._seek_position("90") == "0:01:30"
    assert hometheater._seek_position("0:01:30") == "0:01:30"
    assert hometheater._seek_position("3661") == "1:01:01"


def test_credential_round_trip(tmp_path, monkeypatch):
    from musegadget import config as config_mod
    monkeypatch.setattr(config_mod, "state_dir", lambda: tmp_path)
    hometheater._save_credential("mytv", "Companion", "cred-123", address="10.0.0.5")
    loaded = hometheater._load_credentials()
    assert loaded["mytv"]["Companion"] == "cred-123"
    assert loaded["mytv"]["address"] == "10.0.0.5"
    # file is owner-only
    import os, stat
    mode = stat.S_IMODE(os.stat(tmp_path / "home_theater_credentials.json").st_mode)
    assert mode == 0o600
# -- Pairing backoff ---------------------------------------------------------------

def _patch_pair_interactive(monkeypatch, tmp_path, fake_pyatv, begin_behavior):
    """Common setup for pair_apple_tv tests: temp state dir, PIN 1234, no sleeps."""
    from musegadget import config as config_mod
    monkeypatch.setattr(config_mod, "state_dir", lambda: tmp_path)
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: "1234")

    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    real_pair = fake_pyatv.pair
    attempts = {"begin": 0, "pair": 0}

    async def patched_pair(config, protocol, loop, session=None, storage=None,
                           **kwargs):
        attempts["pair"] += 1
        pairing = await real_pair(config, protocol, loop)

        async def begin():
            attempts["begin"] += 1
            await begin_behavior(attempts["begin"], pairing)

        pairing.begin = begin
        return pairing

    fake_pyatv.pair = patched_pair
    return attempts, sleeps


def test_pair_apple_tv_retries_on_backoff(fake_pyatv, monkeypatch, tmp_path):
    """begin() BackOff answers are honored: wait, then retry with a fresh handler."""

    async def behavior(n, pairing):
        if n < 3:
            raise Exception("Error=BackOff, BackOff=1s, SeqNo=M2")
        pairing.started = True

    attempts, sleeps = _patch_pair_interactive(
        monkeypatch, tmp_path, fake_pyatv, behavior)

    result = hometheater.pair_apple_tv("192.168.68.84")

    assert result == {"paired": True, "device": "Living Room TV",
                      "protocol": "Companion"}
    assert attempts["begin"] == 3
    assert attempts["pair"] == 3  # fresh handler per attempt
    assert sleeps == [2, 2]  # BackOff=1s plus a 1s margin, twice
    loaded = hometheater._load_credentials()
    assert loaded["192.168.68.84"]["Companion"] == "fake-credentials"


def test_pair_apple_tv_does_not_retry_other_errors(fake_pyatv, monkeypatch,
                                                   tmp_path):
    """Non-throttle failures from begin() surface immediately, no retries."""

    async def behavior(n, pairing):
        raise Exception("Error=NotPaired")

    attempts, sleeps = _patch_pair_interactive(
        monkeypatch, tmp_path, fake_pyatv, behavior)

    with pytest.raises(Exception, match="NotPaired"):
        hometheater.pair_apple_tv("192.168.68.84")
    assert attempts["begin"] == 1
    assert sleeps == []


def test_pair_apple_tv_gives_up_after_max_backoffs(fake_pyatv, monkeypatch,
                                                   tmp_path):
    """Persistent throttling eventually raises instead of looping forever."""

    async def behavior(n, pairing):
        raise Exception("Error=BackOff, BackOff=1s, SeqNo=M2")

    attempts, sleeps = _patch_pair_interactive(
        monkeypatch, tmp_path, fake_pyatv, behavior)

    with pytest.raises(Exception, match="BackOff"):
        hometheater.pair_apple_tv("192.168.68.84")
    assert attempts["begin"] == hometheater._PAIR_BEGIN_ATTEMPTS
    assert sleeps == [2] * (hometheater._PAIR_BEGIN_ATTEMPTS - 1)


# -- Pairing: cancel and unpairable targets ------------------------------------------

def _pair_setup(monkeypatch, tmp_path, fake_pyatv, pin_input):
    from musegadget import config as config_mod
    monkeypatch.setattr(config_mod, "state_dir", lambda: tmp_path)
    monkeypatch.setattr("builtins.input", pin_input)
    closed = []
    real_pair = fake_pyatv.pair

    async def patched_pair(config, protocol, loop, session=None, storage=None,
                           **kwargs):
        pairing = await real_pair(config, protocol, loop)

        async def close():
            closed.append(True)

        pairing.close = close
        return pairing

    fake_pyatv.pair = patched_pair
    return closed


@pytest.mark.parametrize("exc", [KeyboardInterrupt, EOFError])
def test_pair_apple_tv_cancel_at_pin_prompt(fake_pyatv, monkeypatch, tmp_path,
                                            exc):
    """Ctrl-C or end of input at the PIN prompt cancels cleanly and closes."""

    def pin_input(*args, **kwargs):
        raise exc

    closed = _pair_setup(monkeypatch, tmp_path, fake_pyatv, pin_input)
    with pytest.raises(hometheater.PairingCancelled):
        hometheater.pair_apple_tv("192.168.68.62")
    assert closed == [True]
    assert hometheater._load_credentials() == {}


def test_pair_apple_tv_empty_pin_cancels(fake_pyatv, monkeypatch, tmp_path):
    closed = _pair_setup(monkeypatch, tmp_path, fake_pyatv,
                         lambda *a, **k: "  ")
    with pytest.raises(hometheater.PairingCancelled, match="no PIN"):
        hometheater.pair_apple_tv("192.168.68.62")
    assert closed == [True]


class _FakePairingRequirement:
    def __init__(self, name):
        self.name = name


class _FakeScanService:
    def __init__(self, pairing):
        self.pairing = _FakePairingRequirement(pairing)


def test_pair_apple_tv_rejects_unsupported_pairing(fake_pyatv, monkeypatch,
                                                   tmp_path):
    """A HomePod lists Companion as Unsupported: fail before begin()."""
    cfg = fake_pyatv.FakeConfig(name="Bedroom (2)", address="192.168.68.84")
    cfg.get_service = lambda protocol: _FakeScanService("Unsupported")
    fake_pyatv.state["configs"] = [cfg]
    began = []

    async def pair(*args, **kwargs):
        began.append(True)

    fake_pyatv.pair = pair
    with pytest.raises(hometheater.HomeTheaterError, match="HomePod"):
        hometheater.pair_apple_tv("192.168.68.84")
    assert began == []


def test_pair_apple_tv_rejects_missing_protocol(fake_pyatv):
    cfg = fake_pyatv.FakeConfig(name="Kitchen")
    cfg.get_service = lambda protocol: None
    fake_pyatv.state["configs"] = [cfg]
    with pytest.raises(hometheater.HomeTheaterError, match="doesn't offer"):
        hometheater.pair_apple_tv("10.0.0.10")


def test_pair_apple_tv_allows_mandatory_pairing(fake_pyatv, monkeypatch,
                                                tmp_path):
    cfg = fake_pyatv.FakeConfig(name="Bedroom", address="192.168.68.62")
    cfg.get_service = lambda protocol: _FakeScanService("Mandatory")
    fake_pyatv.state["configs"] = [cfg]
    _pair_setup(monkeypatch, tmp_path, fake_pyatv, lambda *a, **k: "1234")
    result = hometheater.pair_apple_tv("192.168.68.62")
    assert result["paired"] is True


@pytest.mark.parametrize("exc", [hometheater.PairingCancelled("pairing cancelled"),
                                 KeyboardInterrupt()])
def test_cli_appletv_pair_cancel_exits_quietly(monkeypatch, capsys, exc):
    from musegadget import cli

    def fake_pair(target, protocol):
        raise exc

    monkeypatch.setattr(hometheater, "pair_apple_tv", fake_pair)
    rc = cli.main(["appletv-pair", "--target", "192.168.68.62"])
    assert rc == 130
    err = capsys.readouterr().err
    assert "Pairing cancelled." in err
    assert "Traceback" not in err
