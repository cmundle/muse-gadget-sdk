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

import pytest

from musegadget import config

TOKEN = "mgst_" + "A" * 42 + "w"


def test_sdk_token_is_absent_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv(config.SDK_TOKEN_ENV, raising=False)
    assert config.sdk_token(tmp_path) is None


def test_sdk_token_reads_the_state_file(tmp_path, monkeypatch):
    monkeypatch.delenv(config.SDK_TOKEN_ENV, raising=False)
    (tmp_path / config.SDK_TOKEN_FILE).write_text(TOKEN + "\n")
    assert config.sdk_token(tmp_path) == TOKEN


def test_sdk_token_environment_overrides_the_file(tmp_path, monkeypatch):
    (tmp_path / config.SDK_TOKEN_FILE).write_text("mgst_" + "B" * 43)
    monkeypatch.setenv(config.SDK_TOKEN_ENV, TOKEN)
    assert config.sdk_token(tmp_path) == TOKEN


@pytest.mark.parametrize("bad", ["mgst_short", "mgst_" + "A" * 43 + "x", "mgst_" + "A" * 42 + "B"])
def test_sdk_token_rejects_tokens_gadgets_could_not_issue(tmp_path, monkeypatch, bad):
    monkeypatch.setenv(config.SDK_TOKEN_ENV, bad)
    with pytest.raises(ValueError):
        config.sdk_token(tmp_path)


def test_state_dir_defaults_to_application_support(monkeypatch):
    monkeypatch.delenv(config.STATE_DIR_ENV, raising=False)
    assert config.state_dir() == config._default_state_dir()
    assert str(config.state_dir()).endswith("Library/Application Support/musegadget")


def test_state_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(config.STATE_DIR_ENV, str(tmp_path))
    assert config.state_dir() == tmp_path


def test_socket_path_defaults_inside_state_dir(monkeypatch, tmp_path):
    monkeypatch.setenv(config.STATE_DIR_ENV, str(tmp_path))
    monkeypatch.delenv(config.SOCKET_ENV, raising=False)
    assert config.socket_path() == tmp_path / config.SOCKET_NAME


def test_socket_path_env_override(monkeypatch, tmp_path):
    sock = tmp_path / "custom.sock"
    monkeypatch.setenv(config.SOCKET_ENV, str(sock))
    assert config.socket_path() == sock
