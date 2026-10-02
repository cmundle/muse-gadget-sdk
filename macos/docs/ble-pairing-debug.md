# BLE Pairing Debug Notes (macOS)

Date: 2026-10-02. Hardware: MacBook Pro (Apple silicon), iPhone with Muse app.
SDK: this repo, `macos-port` branch. Tester: Christopher Mundle.

## Summary

The macOS CoreBluetooth peripheral (`ble_server_macos.py`) advertises
correctly — confirmed independently with nRF Connect — but the Muse iPhone
app never discovers it, so pairing cannot complete. The evidence points at
the app's device scanning rather than the peripheral.

## Timeline

- `musegadget pair` on the Mac: logs show `Bluetooth powered on; publishing
  setup service`, `GATT service registered`, `advertising as MuseGadgetA56581`.
  No errors from `startAdvertising`.
- nRF Connect (iPhone) **did** see the peripheral advertising service UUID
  `7fdd3d1c-38ea-46cf-8b46-314ecf5f240c` with the expected local name.
- Muse app (Settings > Devices > Developer mode ON, Bluetooth permission
  granted, app force-quit and reopened, iPhone Bluetooth toggled): Add Device
  screen shows "No devices found" after scanning.
- Toggling Bluetooth on both devices did not change the outcome.
- The peripheral correctly tracked Bluetooth power state in its logs
  (off → on transitions), proving the delegate chain is alive.

## Experiment: manufacturer data

The Linux server advertises manufacturer data (company `0xFFFF` + paired
flag). The macOS port initially did not. A commit added
`CBAdvertisementDataManufacturerDataKey` for parity — after which nRF
Connect **stopped** seeing the peripheral even though `startAdvertising`
still reported success. Reverting restored nRF visibility.

Hypothesis: the combined advertisement (128-bit service UUID + 17-char
local name + manufacturer data ≈ 45 bytes) exceeds what macOS
CoreBluetooth will actually broadcast, and the failure is silent. The
pre-change advertisement (≈ 40 bytes, name spilling into the scan
response) is nRF-visible.

## Open question

With a known-good, nRF-visible advertisement, the Muse app still does not
discover the device. Possible app-side causes: Developer-mode gating not
taking effect, stale scan cache, or the app filtering on something beyond
the service UUID. Worth filing upstream against
`facebookincubator/muse-gadget-sdk` with these notes, since a Linux gadget
advertising the same service would be subject to the same app behavior.

## Not relevant

- The device correctly does NOT appear in iOS Settings > Bluetooth or
  macOS Bluetooth settings; BLE GATT peripherals never appear there.
