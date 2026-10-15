# Licensed under the GPL: https://www.gnu.org/licenses/old-licenses/gpl-2.0.html
# For details: https://github.com/pylint-dev/pylint/blob/main/LICENSE
# Copyright (c) https://github.com/pylint-dev/pylint/blob/main/CONTRIBUTORS.txt

import os
from pathlib import Path
from typing import Any, NoReturn
from unittest import mock
from unittest.mock import patch

from pytest import CaptureFixture

from pylint.lint.pylinter import MANAGER, PyLinter
from pylint.utils import FileState


def raise_exception(*args: Any, **kwargs: Any) -> NoReturn:
    raise ValueError


@patch.object(FileState, "iter_spurious_suppression_messages", raise_exception)
def test_crash_in_file(
    linter: PyLinter, capsys: CaptureFixture[str], tmp_path: Path
) -> None:
    linter.crash_file_path = str(tmp_path / "pylint-crash-%Y")
    linter.check([__file__])
    out, err = capsys.readouterr()
    assert not out
    assert not err
    files = os.listdir(tmp_path)
    assert len(files) == 1
    assert "pylint-crash-20" in str(files[0])
    assert any(m.symbol == "fatal" for m in linter.reporter.messages)


def test_crash_during_linting(
    linter: PyLinter, capsys: CaptureFixture[str], tmp_path: Path
) -> None:
    with mock.patch(
        "pylint.lint.PyLinter.check_astroid_module", side_effect=RuntimeError
    ):
        linter.crash_file_path = str(tmp_path / "pylint-crash-%Y")
        linter.check([__file__])
        out, err = capsys.readouterr()
        assert not out
        assert not err
        files = os.listdir(tmp_path)
        assert len(files) == 1
        assert "pylint-crash-20" in str(files[0])
        assert any(m.symbol == "astroid-error" for m in linter.reporter.messages)


def test_open_pylinter_denied_modules(linter: PyLinter) -> None:
    """Test PyLinter open() adds ignored modules to Astroid manager deny list."""
    MANAGER.module_denylist = {"mod1"}
    try:
        linter.config.ignored_modules = ["mod2", "mod3"]
        linter.open()
        assert MANAGER.module_denylist == {"mod1", "mod2", "mod3"}
    finally:
        MANAGER.module_denylist = set()


def test_open_pylinter_prefer_stubs(linter: PyLinter) -> None:
    try:
        linter.config.prefer_stubs = True
        linter.open()
        assert MANAGER.prefer_stubs
    finally:
        MANAGER.prefer_stubs = False


def test_repeated_check_only_counts_current_run(
    linter: PyLinter, tmp_path: Path
) -> None:
    """Stats and exit status of a second check() must not contain the first run.

    Reusing a linter must not accumulate messages, modules or the bit encoded
    exit status between runs.
    """
    bad_file = tmp_path / "bad_name.py"
    bad_file.write_text('badName = 1\n', encoding="utf8")
    clean_file = tmp_path / "clean.py"
    clean_file.write_text('"""A clean module."""\nGOOD = 1\n', encoding="utf8")

    linter.disable("all")
    linter.enable("invalid-name")
    linter.set_option("persistent", False)
    linter.set_option("score", False)

    linter.check([str(bad_file)])
    assert linter.stats.by_msg == {"invalid-name": 1}
    assert linter.msg_status == 16
    checked = {p.replace(os.sep, "/") for p in linter.stats.modules_names}
    assert str(bad_file).replace(os.sep, "/") in checked

    # The second check targets a clean file: every statistic must describe only
    # this second run.
    linter.check([str(clean_file)])
    assert linter.stats.by_msg == {}
    assert linter.stats.by_module and all(
        module == "clean" for module in linter.stats.by_module
    )
    assert linter.msg_status == 0
    assert not linter.any_fail_on_issues()
    assert all("bad_name" not in name for name in linter.stats.modules_names)
    assert any("clean" in name for name in linter.stats.modules_names)

    # The score is computed from the clean run only and is the maximum even
    # though the first run collected a message.
    score_value = linter.generate_reports()
    assert score_value == 10.0
    assert linter.stats.global_note == 10.0


def test_repeated_check_resets_reporter_messages(
    linter: PyLinter, tmp_path: Path
) -> None:
    """Messages reported after a second check() cannot belong to the first run."""
    bad_file = tmp_path / "bad_name.py"
    bad_file.write_text('badName = 1\n', encoding="utf8")
    clean_file = tmp_path / "clean.py"
    clean_file.write_text('"""A clean module."""\nGOOD = 1\n', encoding="utf8")

    linter.disable("all")
    linter.enable("invalid-name")
    linter.set_option("persistent", False)
    linter.set_option("score", False)

    linter.check([str(bad_file)])
    assert [m.symbol for m in linter.reporter.messages] == ["invalid-name"]

    linter.check([str(clean_file)])
    assert linter.reporter.messages == []


def test_check_resets_stale_state(linter: PyLinter, tmp_path: Path) -> None:
    """A standalone check() starts fresh even when the linter was already used."""
    clean_file = tmp_path / "clean.py"
    clean_file.write_text('"""A clean module."""\nGOOD = 1\n', encoding="utf8")

    linter.disable("all")
    linter.enable("invalid-name")
    linter.set_option("persistent", False)
    linter.set_option("score", False)

    linter.stats.by_msg["stale-message"] = 3
    linter.msg_status = 16
    linter.check([str(clean_file)])
    assert linter.stats.by_msg == {}
    assert linter.msg_status == 0
