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

from __future__ import annotations

import subprocess

from musegadget import network


def _fake_run(output: str):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")
    return run


def test_nmcli_unescapes_colons_and_backslashes(monkeypatch):
    monkeypatch.setattr(network.shutil, "which", lambda name: "/usr/bin/nmcli")
    monkeypatch.setattr(
        network.subprocess, "run", _fake_run("no:Other\nyes:My\\:Net\\\\5G\n")
    )
    assert network.active_wifi_ssid() == "My:Net\\5G"


def test_networksetup_reads_the_current_network(monkeypatch):
    monkeypatch.setattr(
        network.shutil, "which", lambda name: "/usr/sbin/networksetup" if name == "networksetup" else None
    )
    monkeypatch.setattr(
        network.subprocess, "run", _fake_run("Current Wi-Fi Network: Example Net\n")
    )
    assert network.active_wifi_ssid() == "Example Net"
