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

"""CoreBluetooth GATT peripheral for Muse Gadget setup on macOS.

This is the macOS counterpart of the Linux ``ble_server.py`` (BlueZ over
D-Bus). It advertises the same setup service and exposes the same two
characteristics: RX (the phone writes commands) and TX (the device notifies
responses), with the same UUIDs, so the Muse app pairs with it identically.

It implements the ``Transport`` protocol from ``musegadget.ble_setup``:
``send_packets``, ``mtu`` and ``disconnect``, plus ``run`` (blocks serving
until ``stop``) and ``stop``.

.. note::
    The Bluetooth paths below are written carefully against PyObjC
    conventions but are **not yet tested against real Apple hardware**: this
    module was scaffolded on Linux, where ``CoreBluetooth`` cannot even be
    imported. Pair it for real with the Muse app (Settings > Devices >
    Developer mode) before trusting it, and expect to iterate on delegate
    timing and ATT edge cases.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from musegadget.ble_framing import CHUNK_STAGGER_S, MAX_PACKET_BYTES

log = logging.getLogger(__name__)

# Identical to the Linux/ESP32 gadget: the apps look for this service.
SERVICE_UUID = "7fdd3d1c-38ea-46cf-8b46-314ecf5f240c"
RX_UUID = "4d593029-28a2-4a6e-a1f0-3c2d5e8f9b01"
TX_UUID = "d75dc4ca-7b2b-4e9c-8f0a-1d2e3f4a5b6c"

# Until the stack reports a usable notify size, assume packets no larger than
# the framing layer's maximum, exactly like the Linux server does. Phones
# running the Muse app negotiate at least this.
_ASSUMED_MTU = MAX_PACKET_BYTES + 3

try:
    import objc
    from CoreBluetooth import (
        CBATTErrorSuccess,
        CBAdvertisementDataLocalNameKey,
        CBAdvertisementDataServiceUUIDsKey,
        CBAttributePermissionsReadable,
        CBAttributePermissionsWriteable,
        CBCharacteristicPropertyNotify,
        CBCharacteristicPropertyRead,
        CBCharacteristicPropertyWrite,
        CBCharacteristicPropertyWriteWithoutResponse,
        CBMutableCharacteristic,
        CBMutableService,
        CBPeripheralManager,
        CBPeripheralManagerStatePoweredOn,
        CBUUID,
    )
    from Foundation import NSData, NSDate, NSRunLoop, NSObject

    _HAVE_CORE_BLUETOOTH = True
except ImportError:
    _HAVE_CORE_BLUETOOTH = False


if _HAVE_CORE_BLUETOOTH:

    class _PeripheralDelegate(NSObject):
        """Forwards CoreBluetooth peripheral callbacks into the server.

        Only defined when CoreBluetooth imported successfully; the hardware
        paths are untested (see module docstring).
        """

        def initWithServer_(self, server):
            self = objc.super(_PeripheralDelegate, self).init()
            if self is None:
                return None
            self._server = server
            return self

        # -- CBPeripheralManagerDelegate ----------------------------------

        def peripheralManagerDidUpdateState_(self, peripheral):
            # NOTE: hardware-untested. Expected flow: PoweredOn -> build the
            # service, add it, then start advertising from didAddService.
            if peripheral.state() == CBPeripheralManagerStatePoweredOn:
                log.info("Bluetooth powered on; publishing setup service")
                self._server._publish_service(peripheral)
            else:
                log.warning("Bluetooth not powered on (state %s); waiting", peripheral.state())

        def peripheralManager_didAddService_error_(self, peripheral, service, error):
            if error is not None:
                log.error("could not add GATT service: %s", error)
                return
            log.info("GATT service registered")
            peripheral.startAdvertising_(
                {
                    CBAdvertisementDataLocalNameKey: self._server._local_name,
                    CBAdvertisementDataServiceUUIDsKey: [
                        CBUUID.UUIDWithString_(SERVICE_UUID)
                    ],
                }
            )

        def peripheralManagerDidStartAdvertising_error_(self, peripheral, error):
            if error is not None:
                log.error("advertising failed: %s", error)
            else:
                log.info("advertising as %s", self._server._local_name)

        def peripheralManager_didReceiveWriteRequests_(self, peripheral, requests):
            # NOTE: hardware-untested. RX accepts both write types; respond to
            # every request so write-with-response centrals are acknowledged.
            for request in requests:
                value = request.value()
                data = bytes(value) if value is not None else b""
                self._server._handle_write(data)
                peripheral.respondToRequest_withResult_(request, CBATTErrorSuccess)

        def peripheralManager_central_didSubscribeToCharacteristic_(
            self, peripheral, central, characteristic
        ):
            log.info("central subscribed to TX notifications")
            self._server._subscribed = True

        def peripheralManager_central_didUnsubscribeFromCharacteristic_(
            self, peripheral, central, characteristic
        ):
            log.info("central unsubscribed from TX notifications")
            # Best effort: an unsubscribe is the closest signal we get to the
            # central going away (there is no peripheral-side disconnect API).
            self._server._subscribed = False
            self._server._on_disconnect()

        def peripheralManagerIsReadyToUpdateSubscribers_(self, peripheral):
            self._server._tx_ready.set()


class BleServerMacOS:
    """Owns Bluetooth while setup is open; implements the setup Transport."""

    def __init__(
        self,
        local_name: str,
        on_write: Callable[[bytes], None],
        on_disconnect: Callable[[], None],
    ) -> None:
        self._local_name = local_name
        self._on_write = on_write
        self._on_disconnect = on_disconnect
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._tx_ready = threading.Event()
        self._tx_ready.set()
        self._subscribed = False
        self._delegate = None
        self._manager = None
        self._tx_char = None

    # -- Transport ----------------------------------------------------------

    def mtu(self) -> int:
        return _ASSUMED_MTU

    def send_packets(self, packets: list[bytes]) -> None:
        for i, packet in enumerate(packets):
            if i:
                time.sleep(CHUNK_STAGGER_S)
            self._send_packet_blocking(packet)

    def disconnect(self, delay: float) -> None:
        # CoreBluetooth offers no peripheral-side disconnect: the best we can
        # do is stop advertising so no new centrals connect. Documented as a
        # known limitation versus the Linux server.
        def _stop_advertising() -> None:
            manager = self._manager
            if manager is not None:
                try:
                    manager.stopAdvertising()
                except Exception as exc:  # noqa: BLE002 - hardware-untested path
                    log.warning("stopAdvertising failed: %s", exc)

        threading.Timer(delay, _stop_advertising).start()

    # -- Lifecycle ----------------------------------------------------------

    def run(self) -> None:
        """Start advertising and serve until :meth:`stop`. Blocks."""
        if not _HAVE_CORE_BLUETOOTH:
            raise RuntimeError(
                "CoreBluetooth is unavailable: pairing needs macOS with "
                "pyobjc-framework-CoreBluetooth installed"
            )
        # The manager must live on a thread with a run loop; delegate
        # callbacks arrive on the main thread because queue=None.
        self._delegate = _PeripheralDelegate.alloc().initWithServer_(self)
        self._manager = CBPeripheralManager.alloc().initWithDelegate_queue_(
            self._delegate, None
        )
        runloop = NSRunLoop.currentRunLoop()
        try:
            while not self._stopped.is_set():
                runloop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.2))
        finally:
            self._teardown()

    def stop(self) -> None:
        self._stopped.set()

    # -- Internals ----------------------------------------------------------

    def _publish_service(self, peripheral) -> None:
        # NOTE: hardware-untested.
        rx_char = CBMutableCharacteristic.alloc().initWithType_properties_value_permissions_(
            CBUUID.UUIDWithString_(RX_UUID),
            CBCharacteristicPropertyWrite | CBCharacteristicPropertyWriteWithoutResponse,
            None,
            CBAttributePermissionsWriteable,
        )
        tx_char = CBMutableCharacteristic.alloc().initWithType_properties_value_permissions_(
            CBUUID.UUIDWithString_(TX_UUID),
            CBCharacteristicPropertyRead | CBCharacteristicPropertyNotify,
            None,
            CBAttributePermissionsReadable,
        )
        service = CBMutableService.alloc().initWithType_primary_(
            CBUUID.UUIDWithString_(SERVICE_UUID), True
        )
        service.setCharacteristics_([rx_char, tx_char])
        self._tx_char = tx_char
        peripheral.addService_(service)

    def _teardown(self) -> None:
        manager = self._manager
        if manager is None:
            return
        try:
            manager.stopAdvertising()
            manager.removeAllServices()
        except Exception as exc:  # noqa: BLE002 - hardware-untested path
            log.warning("teardown failed: %s", exc)
        finally:
            self._manager = None
            self._tx_char = None

    def _handle_write(self, data: bytes) -> None:
        log.debug("RX %d bytes", len(data))
        self._on_write(data)

    def _notify(self, packet: bytes) -> bool:
        """Push one packet as a TX notification. False when the queue is full."""
        data = NSData.dataWithBytes_length_(packet, len(packet))
        return bool(
            self._manager.updateValue_forCharacteristic_onSubscribedCentrals_(
                data, self._tx_char, None
            )
        )

    def _send_packet_blocking(self, packet: bytes) -> bool:
        while not self._stopped.is_set():
            if self._notify(packet):
                return True
            # TX queue full: wait until the stack is ready for more.
            self._tx_ready.clear()
            self._tx_ready.wait(timeout=5.0)
        return False
