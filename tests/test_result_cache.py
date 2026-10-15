# Licensed under the GPL: https://www.gnu.org/licenses/old-licenses/gpl-2.0.html
# For details: https://github.com/pylint-dev/pylint/blob/main/LICENSE
# Copyright (c) https://github.com/pylint-dev/pylint/blob/main/CONTRIBUTORS.txt

"""Tests for the persistent result cache.

The cache stores the :class:`~pylint.utils.LinterStats` of a run in
``PYLINTHOME`` so that the next run can display a rating delta. It must only be
reused when everything that can change the messages or the rating is identical
between two runs, and a corrupted or stale entry must be discarded.
"""

# Pytest fixtures work like this by design
# pylint: disable=redefined-outer-name

from __future__ import annotations

import functools
import io
import json
import os
import pickle
import re
import subprocess
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pylint.lint import PyLinter, get_cache_key
from pylint.lint import caching as caching_module
from pylint.lint import pylinter as pylinter_module
from pylint.lint.caching import _get_pdata_path, load_results, save_results
from pylint.reporters.text import TextReporter
from pylint.utils import LinterStats

REPO_ROOT = Path(__file__).resolve().parents[1]

MODULE_NAME = "cached_module"

CODE = '''"""A module used to exercise the result cache."""


def BadName():
    unused_value = 1
    return unused_value
'''


@pytest.fixture
def data_file(tmp_path: Path) -> Path:
    return _get_pdata_path(Path(MODULE_NAME), 1, tmp_path)


# get_cache_key ##############################################################


def test_cache_key_is_stable_for_equal_configuration() -> None:
    """Ordering of dicts and sets must not change the key."""
    config_a: dict[str, Any] = {
        "x": 1,
        "values": {3, 1, 2},
        "nested": {"b": 2, "a": [1, 2]},
        "pattern": re.compile("^abc$"),
        "py_version": (3, 10),
    }
    config_b: dict[str, Any] = {
        "py_version": (3, 10),
        "pattern": re.compile("^abc$"),
        "nested": {"a": [1, 2], "b": 2},
        "values": {1, 2, 3},
        "x": 1,
    }
    assert get_cache_key(config_a) == get_cache_key(config_b)


@pytest.mark.parametrize(
    "changed",
    [
        {"x": 2},
        {"values": {1, 2}},
        {"pattern": re.compile("^abcd$")},
        {"py_version": (3, 9)},
        {"extra": True},
    ],
)
def test_cache_key_changes_when_configuration_changes(changed: dict[str, Any]) -> None:
    base: dict[str, Any] = {
        "x": 1,
        "values": {3, 1, 2},
        "nested": {"b": 2, "a": [1, 2]},
        "pattern": re.compile("^abc$"),
        "py_version": (3, 10),
    }
    different = {**base, **changed}
    assert get_cache_key(base) != get_cache_key(different)


# load_results / save_results ################################################


def test_save_and_load_hit_on_matching_key(
    tmp_path: Path, data_file: Path
) -> None:
    stats = LinterStats()
    stats.global_note = 7.5
    save_results(stats, MODULE_NAME, tmp_path, config_hash="config-hash")
    loaded = load_results(MODULE_NAME, tmp_path, config_hash="config-hash")
    assert loaded is not None
    assert loaded.global_note == 7.5
    assert data_file.exists()


def test_save_and_load_without_hash_still_works(tmp_path: Path) -> None:
    """The cache remains usable by callers that do not validate a hash."""
    save_results(LinterStats(), MODULE_NAME, tmp_path)
    loaded = load_results(MODULE_NAME, tmp_path)
    assert isinstance(loaded, LinterStats)


def test_load_misses_when_configuration_changed(
    tmp_path: Path, data_file: Path
) -> None:
    save_results(LinterStats(), MODULE_NAME, tmp_path, config_hash="old-config")
    assert load_results(MODULE_NAME, tmp_path, config_hash="new-config") is None
    # The stale entry is cleaned up instead of being kept around forever.
    assert not data_file.exists()


def test_corrupted_pickle_is_discarded(tmp_path: Path, data_file: Path) -> None:
    data_file.parent.mkdir(parents=True, exist_ok=True)
    data_file.write_bytes(b"this is definitely not a pickle")
    assert load_results(MODULE_NAME, tmp_path, config_hash="hash") is None
    assert not data_file.exists()


def test_truncated_pickle_is_discarded(tmp_path: Path, data_file: Path) -> None:
    save_results(LinterStats(), MODULE_NAME, tmp_path, config_hash="hash")
    raw = data_file.read_bytes()
    data_file.write_bytes(raw[: len(raw) // 2])
    assert load_results(MODULE_NAME, tmp_path, config_hash="hash") is None
    assert not data_file.exists()


def test_legacy_stats_entry_is_reusable_only_without_hash(
    tmp_path: Path, data_file: Path
) -> None:
    """An entry written before cache keys existed cannot be validated."""
    legacy_stats = LinterStats()
    with open(data_file, "wb") as stream:
        pickle.dump(legacy_stats, stream)

    assert isinstance(load_results(MODULE_NAME, tmp_path), LinterStats)

    with open(data_file, "wb") as stream:
        pickle.dump(legacy_stats, stream)
    assert load_results(MODULE_NAME, tmp_path, config_hash="hash") is None
    assert not data_file.exists()


def test_invalid_payload_warns_and_is_discarded(
    tmp_path: Path, data_file: Path
) -> None:
    # type ignore because this is what we're testing
    save_results(1, MODULE_NAME, tmp_path)  # type: ignore[arg-type]
    with pytest.warns(UserWarning, match="old pylint cache with invalid data"):
        assert load_results(MODULE_NAME, tmp_path) is None
    assert not data_file.exists()


def test_save_is_atomic_and_leaves_no_temp_files(
    tmp_path: Path, data_file: Path
) -> None:
    save_results(LinterStats(), MODULE_NAME, tmp_path, config_hash="hash")
    assert not list(tmp_path.glob("*.tmp"))
    assert [path.name for path in tmp_path.iterdir()] == [data_file.name]


def test_save_honors_pylint_home_argument(tmp_path: Path, data_file: Path) -> None:
    """Regression test: ``pylint_home`` used to be ignored when saving."""
    other_home = tmp_path / "other_home"
    save_results(LinterStats(), MODULE_NAME, other_home, config_hash="hash")
    assert data_file.exists() is False
    assert _get_pdata_path(Path(MODULE_NAME), 1, other_home).exists()


# Linter integration #########################################################


@pytest.fixture
def configured_linter() -> PyLinter:
    linter = PyLinter()
    linter.load_default_plugins()
    return linter


@pytest.fixture
def isolated_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Callable[..., Path]:
    """Redirect the result cache reads/writes of the linter to ``tmp_path``.

    The linter imports ``load_results`` / ``save_results`` directly, so the
    module-level symbols are rebound with ``pylint_home`` pre-bound.
    """

    def use_cache_dir(cache_dir: Path = tmp_path) -> Path:
        monkeypatch.setattr(
            pylinter_module,
            "load_results",
            functools.partial(caching_module.load_results, pylint_home=cache_dir),
        )
        monkeypatch.setattr(
            pylinter_module,
            "save_results",
            functools.partial(caching_module.save_results, pylint_home=cache_dir),
        )
        return cache_dir

    return use_cache_dir


def _lint(source_file: Path, arguments: list[str]) -> str:
    output = io.StringIO()
    linter = PyLinter()
    linter.load_default_plugins()
    linter.set_reporter(TextReporter(output))
    linter._parse_command_line_configuration([*arguments, str(source_file)])
    linter.check([str(source_file)])
    linter.generate_reports()
    return output.getvalue()


def _messages_only(output: str) -> str:
    return output.split("Your code has been rated at", 1)[0].strip()


def test_linter_key_covers_options(configured_linter: PyLinter) -> None:
    before = configured_linter._get_result_cache_key()
    configured_linter.set_option(
        "max-line-length", configured_linter.config.max_line_length + 1
    )
    assert configured_linter._get_result_cache_key() != before


def test_linter_key_covers_py_version(configured_linter: PyLinter) -> None:
    before = configured_linter._get_result_cache_key()
    configured_linter.config.py_version = (3, 8)
    assert configured_linter._get_result_cache_key() != before


def test_linter_key_covers_plugins(
    configured_linter: PyLinter, tmp_path: Path
) -> None:
    plugin_file = tmp_path / "my_test_plugin.py"
    plugin_file.write_text("__version__ = '1.0'\n", encoding="utf-8")
    plugin = types.ModuleType("my_test_plugin")
    plugin.__version__ = "1.0"
    plugin.__file__ = str(plugin_file)
    configured_linter._dynamic_plugins = {"my_test_plugin": plugin}

    before = configured_linter._get_result_cache_key()
    plugin.__version__ = "2.0"
    assert configured_linter._get_result_cache_key() != before

    plugin.__version__ = "1.0"
    assert configured_linter._get_result_cache_key() == before

    # The boolean representation used in parallel workers must not crash and
    # must distinguish a successfully loaded plugin from a failed one.
    configured_linter._dynamic_plugins = {"my_test_plugin": True}
    loaded_key = configured_linter._get_result_cache_key()
    configured_linter._dynamic_plugins = {
        "my_test_plugin": ModuleNotFoundError("my_test_plugin")
    }
    assert configured_linter._get_result_cache_key() != loaded_key


def test_cache_is_reused_for_identical_configuration(
    tmp_path: Path, isolated_cache: Callable[..., Path]
) -> None:
    cache_dir = isolated_cache()
    source_file = tmp_path / f"{MODULE_NAME}.py"
    source_file.write_text(CODE, encoding="utf-8")

    first_run = _lint(source_file, [])
    assert "previous run" not in first_run

    second_run = _lint(source_file, [])
    assert "previous run" in second_run
    assert len(list(cache_dir.glob("*.stats"))) == 1


def test_cache_is_not_reused_after_configuration_change(
    tmp_path: Path, isolated_cache: Callable[..., Path]
) -> None:
    isolated_cache()
    source_file = tmp_path / f"{MODULE_NAME}.py"
    source_file.write_text(CODE, encoding="utf-8")

    _lint(source_file, [])
    changed_run = _lint(source_file, ["--py-version=3.8"])
    assert "previous run" not in changed_run

    # The new configuration populates a fresh, reusable cache entry of its own.
    changed_rerun = _lint(source_file, ["--py-version=3.8"])
    assert "previous run" in changed_rerun


def test_corrupted_cache_falls_back_to_a_full_check(
    tmp_path: Path, isolated_cache: Callable[..., Path]
) -> None:
    cache_dir = isolated_cache()
    source_file = tmp_path / f"{MODULE_NAME}.py"
    source_file.write_text(CODE, encoding="utf-8")

    _lint(source_file, [])
    [entry] = cache_dir.glob("*.stats")
    entry.write_bytes(b"corrupted")

    rerun = _lint(source_file, [])
    assert "rated at" in rerun
    assert "previous run" not in rerun

    # The bad entry was replaced by a fresh, valid one.
    with open(entry, "rb") as stream:
        payload = pickle.load(stream)
    assert isinstance(payload, dict)
    assert isinstance(payload["stats"], LinterStats)


def test_disabled_cache_is_neither_read_nor_written(
    tmp_path: Path, isolated_cache: Callable[..., Path]
) -> None:
    cache_dir = isolated_cache()
    source_file = tmp_path / f"{MODULE_NAME}.py"
    source_file.write_text(CODE, encoding="utf-8")

    output = _lint(source_file, ["--persistent=no"])
    assert not list(cache_dir.glob("*.stats"))
    assert "previous run" not in output


def test_toggling_the_cache_does_not_change_messages(
    tmp_path: Path, isolated_cache: Callable[..., Path]
) -> None:
    isolated_cache()
    source_file = tmp_path / f"{MODULE_NAME}.py"
    source_file.write_text(CODE, encoding="utf-8")

    enabled = _messages_only(_lint(source_file, []))
    disabled = _messages_only(_lint(source_file, ["--persistent=no"]))
    assert enabled == disabled


# Parallel checking ##########################################################


def _run_pylint_on_package(
    arguments: list[str], cache_dir: Path, package_dir: Path
) -> tuple[int, dict[str, Any]]:
    environment = os.environ.copy()
    environment["PYLINTHOME"] = str(cache_dir)
    environment["PYTHONPATH"] = (
        f"{REPO_ROOT}{os.pathsep}{environment.get('PYTHONPATH', '')}"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pylint",
            "--rcfile=",
            "--output-format=json2",
            *arguments,
            str(package_dir),
        ],
        # Run from the package directory so that pylint does not pick up the
        # pylintrc of the pylint repository itself.
        cwd=package_dir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert completed.returncode != 32, completed.stderr
    return completed.returncode, json.loads(completed.stdout)


@pytest.mark.needs_two_cores
def test_parallel_and_serial_checks_emit_the_same_messages(
    tmp_path: Path,
) -> None:
    """The result cache and ``--jobs`` must not alter the message set."""
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    for name in ("alpha", "beta", "gamma"):
        (package_dir / f"{name}.py").write_text(CODE, encoding="utf-8")

    def message_signature(arguments: list[str]) -> set[tuple[Any, ...]]:
        _, report = _run_pylint_on_package(arguments, tmp_path / "cache", package_dir)
        return {
            (
                message["messageId"],
                message["symbol"],
                message["module"],
                message["line"],
                message["column"],
            )
            for message in report["messages"]
        }

    serial = message_signature(["--jobs=1", "--persistent=no"])
    parallel = message_signature(["--jobs=2", "--persistent=no"])
    cached_serial = message_signature(["--jobs=1"])
    cached_parallel = message_signature(["--jobs=2"])

    assert serial
    assert parallel == serial
    assert cached_serial == serial
    assert cached_parallel == serial
