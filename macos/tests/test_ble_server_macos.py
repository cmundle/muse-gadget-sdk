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

"""Tests for the macOS CoreBluetooth peripheral.

The real Bluetooth paths need Apple hardware, so these tests exercise the
Transport interface (UUIDs, MTU, packet pacing, write/disconnect plumbing)
with the CoreBluetooth layer stubbed out.
"""

from __future__ import annotations

import time

import pytest

from musegadget import ble_server_macos
from musegadget.ble_framing import MAX_PACKET_BYTES
from musegadget.ble_server_macos import BleServerMacOS


def test_uuids_match_the_linux_server():
    # The Muse app looks for this exact service; the values must stay in
    # sync with linux/src/musegadget/ble_server.py.
    assert ble_server_macos.SERVICE_UUID == "7fdd3d1c-38ea-46cf-8b46-314ecf5f240c"
    assert ble_server_macos.RX_UUID == "4d593029-28a2-4a6e-a1f0-3c2d5e8f9b01"
    assert ble_server_macos.TX_UUID == "d75dc4ca-7b2b-4e9c-8f0a-1d2e3f4a5b6c"


def test_mtu_matches_the_framing_maximum():
    server = BleServerMacOS("MuseGadgetABCDEF", on_write=lambda b: None,
                            on_disconnect=lambda: None)
    assert server.mtu() == MAX_PACKET_BYTES + 3


def test_run_requires_core_bluetooth():
    server = BleServerMacOS("MuseGadgetABCDEF", on_write=lambda b: None,
                            on_disconnect=lambda: None)
    if ble_server_macos._HAVE_CORE_BLUETOOTH:
        pytest.skip("CoreBluetooth is importable here; nothing to assert")
    with pytest.raises(RuntimeError, match="CoreBluetooth is unavailable"):
        server.run()


class _RecordingServer(BleServerMacOS):
    """BleServerMacOS with the notify path stubbed."""

    def __init__(self, *args, fail_first=0, **kwargs):
        super().__init__(*args, **kwargs)
        self.sent: list[bytes] = []
        self._fail_first = fail_first

    def _notify(self, packet: bytes) -> bool:
        if self._fail_first > 0:
            self._fail_first -= 1
            return False
        self.sent.append(packet)
        return True


def test_send_packets_delivers_in_order_with_stagger(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    server = _RecordingServer("MuseGadgetABCDEF", on_write=lambda b: None,
                              on_disconnect=lambda: None)
    packets = [b"one", b"two", b"three"]
    server.send_packets(packets)
    assert server.sent == packets
    # One stagger sleep between each pair of packets, none before the first.
    assert sleeps == [ble_server_macos.CHUNK_STAGGER_S] * (len(packets) - 1)


def test_send_packets_retries_when_the_tx_queue_is_full(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)

    server = _RecordingServer("MuseGadgetABCDEF", on_write=lambda b: None,
                              on_disconnect=lambda: None, fail_first=2)
    waits: list[float | None] = []
    server._tx_ready.wait = lambda timeout=None: waits.append(timeout) or True  # type: ignore[method-assign]
    server.send_packets([b"pkt"])
    assert server.sent == [b"pkt"]
    # Two failed notifies, each followed by a wait for stack readiness.
    assert waits == [5.0, 5.0]


def test_handle_write_forwards_to_on_write():
    received: list[bytes] = []
    server = BleServerMacOS("MuseGadgetABCDEF", on_write=received.append,
                            on_disconnect=lambda: None)
    server._handle_write(b"\xfe\x00\x01payload")
    assert received == [b"\xfe\x00\x01payload"]


def test_disconnect_stops_advertising_after_the_delay(monkeypatch):
    stopped: list[bool] = []

    class _Manager:
        def stopAdvertising(self):  # noqa: N802 - matches ObjC selector
            stopped.append(True)

    server = BleServerMacOS("MuseGadgetABCDEF", on_write=lambda b: None,
                            on_disconnect=lambda: None)
    server._manager = _Manager()  # type: ignore[assignment]

    fired: list[float] = []

    class _ImmediateTimer:
        def __init__(self, delay, fn):
            fired.append(delay)
            self._fn = fn

        def start(self):
            self._fn()

    monkeypatch.setattr(ble_server_macos.threading, "Timer", _ImmediateTimer)
    server.disconnect(1.5)
    assert fired == [1.5]
    assert stopped == [True]


def test_stop_unblocks_a_waiting_send(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    server = _RecordingServer("MuseGadgetABCDEF", on_write=lambda b: None,
                              on_disconnect=lambda: None, fail_first=10**9)
    server._tx_ready.set()  # avoid blocking on the readiness event
    server.stop()
    assert server._send_packet_blocking(b"pkt") is False
