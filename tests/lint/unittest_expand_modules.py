# Licensed under the GPL: https://www.gnu.org/licenses/old-licenses/gpl-2.0.html
# For details: https://github.com/pylint-dev/pylint/blob/main/LICENSE
# Copyright (c) https://github.com/pylint-dev/pylint/blob/main/CONTRIBUTORS.txt

from __future__ import annotations

import copy
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from pylint.checkers import BaseChecker
from pylint.lint import PyLinter
from pylint.lint.expand_modules import _is_in_ignore_list_re, expand_modules
from pylint.testutils import CheckerTestCase, GenericTestReporter, set_config
from pylint.typing import MessageDefinitionTuple, ModuleDescriptionDict


def test__is_in_ignore_list_re_match() -> None:
    patterns = [
        re.compile(".*enchilada.*"),
        re.compile("unittest_.*"),
        re.compile(".*tests/.*"),
    ]
    assert _is_in_ignore_list_re("unittest_utils.py", patterns)
    assert _is_in_ignore_list_re("cheese_enchiladas.xml", patterns)
    assert _is_in_ignore_list_re("src/tests/whatever.xml", patterns)


TEST_DIRECTORY = Path(__file__).parent.parent
INIT_PATH = str(TEST_DIRECTORY / "lint/__init__.py")
EXPAND_MODULES_BASE = "unittest_expand_modules.py"
EXPAND_MODULES = str(TEST_DIRECTORY / "lint" / EXPAND_MODULES_BASE)
this_file = {
    "basename": "lint.unittest_expand_modules",
    "basepath": EXPAND_MODULES,
    "isarg": True,
    "name": "lint.unittest_expand_modules",
    "path": EXPAND_MODULES,
    "isignored": False,
}

this_file_relative_to_parent = {
    "basename": "lint.unittest_expand_modules",
    "basepath": EXPAND_MODULES_BASE,
    "isarg": True,
    "name": "lint.unittest_expand_modules",
    "path": EXPAND_MODULES_BASE,
    "isignored": False,
}

this_file_from_init = {
    "basename": "lint",
    "basepath": INIT_PATH,
    "isarg": False,
    "name": "lint.unittest_expand_modules",
    "path": EXPAND_MODULES,
    "isignored": False,
}

this_file_from_init_deduplicated = {
    "basename": "lint",
    "basepath": INIT_PATH,
    "isarg": True,
    "name": "lint.unittest_expand_modules",
    "path": EXPAND_MODULES,
    "isignored": False,
}

unittest_discover_files = {
    "basename": "lint",
    "basepath": INIT_PATH,
    "isarg": False,
    "name": "lint.unittest_discover_files",
    "path": str(TEST_DIRECTORY / "lint/unittest_discover_files.py"),
    "isignored": False,
}

unittest_lint = {
    "basename": "lint",
    "basepath": INIT_PATH,
    "isarg": False,
    "name": "lint.unittest_lint",
    "path": str(TEST_DIRECTORY / "lint/unittest_lint.py"),
    "isignored": False,
}

test_utils = {
    "basename": "lint",
    "basepath": INIT_PATH,
    "isarg": False,
    "name": "lint.test_utils",
    "path": str(TEST_DIRECTORY / "lint/test_utils.py"),
    "isignored": False,
}

test_run_pylint = {
    "basename": "lint",
    "basepath": INIT_PATH,
    "isarg": False,
    "name": "lint.test_run_pylint",
    "path": str(TEST_DIRECTORY / "lint/test_run_pylint.py"),
    "isignored": False,
}

test_pylinter = {
    "basename": "lint",
    "basepath": INIT_PATH,
    "isarg": False,
    "name": "lint.test_pylinter",
    "path": str(TEST_DIRECTORY / "lint/test_pylinter.py"),
    "isignored": False,
}

test_caching = {
    "basename": "lint",
    "basepath": INIT_PATH,
    "isarg": False,
    "name": "lint.test_caching",
    "path": str(TEST_DIRECTORY / "lint/test_caching.py"),
    "isignored": False,
}

init_of_package = {
    "basename": "lint",
    "basepath": INIT_PATH,
    "isarg": True,
    "name": "lint",
    "path": INIT_PATH,
    "isignored": False,
}

# A directory that is not a python package.
REPORTERS_PATH = Path(__file__).parent.parent / "reporters"
test_reporters = {  # pylint: disable=consider-using-namedtuple-or-dataclass
    str(REPORTERS_PATH / "__init__.py"): {
        "basename": "reporters",
        "basepath": str(REPORTERS_PATH / "__init__.py"),
        "isarg": True,
        "name": "reporters",
        "path": str(REPORTERS_PATH / "__init__.py"),
        "isignored": False,
    },
    str(REPORTERS_PATH / "unittest_json_reporter.py"): {
        "basename": "reporters",
        "basepath": str(REPORTERS_PATH / "__init__.py"),
        "isarg": False,
        "name": "reporters.unittest_json_reporter",
        "path": str(REPORTERS_PATH / "unittest_json_reporter.py"),
        "isignored": False,
    },
    str(REPORTERS_PATH / "unittest_reporting.py"): {
        "basename": "reporters",
        "basepath": str(REPORTERS_PATH / "__init__.py"),
        "isarg": False,
        "path": str(REPORTERS_PATH / "unittest_reporting.py"),
        "name": "reporters.unittest_reporting",
        "isignored": False,
    },
}


def _list_expected_package_modules(
    deduplicating: bool = False,
) -> tuple[dict[str, object], ...]:
    """Generates reusable list of modules for our package."""
    return (
        init_of_package,
        test_caching,
        test_pylinter,
        test_run_pylint,
        test_utils,
        this_file_from_init_deduplicated if deduplicating else this_file_from_init,
        unittest_discover_files,
        unittest_lint,
    )


def _list_expected_package_modules_relative() -> tuple[dict[str, object], ...]:
    """Generates reusable list of modules for our package with relative path input."""
    abs_result = copy.deepcopy(_list_expected_package_modules())
    for item in abs_result:
        assert isinstance(item["basepath"], str)
        assert isinstance(item["path"], str)
        item["basepath"] = os.path.relpath(item["basepath"], str(Path(__file__).parent))
        item["path"] = os.path.relpath(item["path"], str(Path(__file__).parent))
    return abs_result


@contextmanager
def pushd(path: Path) -> Iterator[None]:
    prev = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


class TestExpandModules(CheckerTestCase):
    """Test the expand_modules function while allowing options to be set."""

    class Checker(BaseChecker):
        """This dummy checker is needed to allow options to be set."""

        name = "checker"
        msgs: dict[str, MessageDefinitionTuple] = {}
        options = (("test-opt", {"action": "store_true", "help": "help message"}),)

    CHECKER_CLASS: type = Checker

    @pytest.mark.parametrize(
        "files_or_modules,expected",
        [
            ([__file__], {this_file["path"]: this_file}),
            (
                [str(Path(__file__).parent)],
                {
                    module["path"]: module  # pylint: disable=unsubscriptable-object
                    for module in _list_expected_package_modules()
                },
            ),
            ([str(Path(__file__).parent.parent / "reporters")], test_reporters),
        ],
    )
    @set_config(ignore_paths="")
    def test_expand_modules(
        self, files_or_modules: list[str], expected: dict[str, ModuleDescriptionDict]
    ) -> None:
        """Test expand_modules with the default value of ignore-paths."""
        ignore_list: list[str] = []
        ignore_list_re: list[re.Pattern[str]] = []
        modules, errors = expand_modules(
            files_or_modules,
            [],
            ignore_list,
            ignore_list_re,
            self.linter.config.ignore_paths,
        )
        assert modules == expected
        assert not errors

    @pytest.mark.parametrize(
        "files_or_modules,expected",
        [
            (
                [Path(__file__).name],
                {this_file_relative_to_parent["path"]: this_file_relative_to_parent},
            ),
            (
                ["./"],
                {
                    module["path"]: module  # pylint: disable=unsubscriptable-object
                    for module in _list_expected_package_modules_relative()
                },
            ),
        ],
    )
    @set_config(ignore_paths="")
    def test_expand_modules_relative_path(
        self, files_or_modules: list[str], expected: dict[str, ModuleDescriptionDict]
    ) -> None:
        """Test expand_modules with the default value of ignore-paths and relative path as input."""
        ignore_list: list[str] = []
        ignore_list_re: list[re.Pattern[str]] = []
        with pushd(Path(__file__).parent):
            modules, errors = expand_modules(
                files_or_modules,
                [],
                ignore_list,
                ignore_list_re,
                self.linter.config.ignore_paths,
            )
            assert modules == expected
            assert not errors

    @pytest.mark.parametrize(
        "files_or_modules,expected",
        [
            ([__file__, __file__], {this_file["path"]: this_file}),
            (
                [EXPAND_MODULES, str(Path(__file__).parent), EXPAND_MODULES],
                {
                    module["path"]: module  # pylint: disable=unsubscriptable-object
                    for module in _list_expected_package_modules(deduplicating=True)
                },
            ),
        ],
    )
    @set_config(ignore_paths="")
    def test_expand_modules_deduplication(
        self, files_or_modules: list[str], expected: dict[str, ModuleDescriptionDict]
    ) -> None:
        """Test expand_modules deduplication."""
        ignore_list: list[str] = []
        ignore_list_re: list[re.Pattern[str]] = []
        modules, errors = expand_modules(
            files_or_modules,
            [],
            ignore_list,
            ignore_list_re,
            self.linter.config.ignore_paths,
        )
        assert modules == expected
        assert not errors

    @pytest.mark.parametrize(
        "files_or_modules,expected",
        [
            ([__file__], {}),
            (
                [str(Path(__file__).parent)],
                {
                    module["path"]: module  # pylint: disable=unsubscriptable-object
                    for module in (init_of_package,)
                },
            ),
        ],
    )
    @set_config(ignore_paths=".*/lint/.*")
    def test_expand_modules_with_ignore(
        self, files_or_modules: list[str], expected: dict[str, ModuleDescriptionDict]
    ) -> None:
        """Test expand_modules with a non-default value of ignore-paths."""
        ignore_list: list[str] = []
        ignore_list_re: list[re.Pattern[str]] = []
        modules, errors = expand_modules(
            files_or_modules,
            [],
            ignore_list,
            ignore_list_re,
            self.linter.config.ignore_paths,
        )
        assert {k: v for k, v in modules.items() if not v["isignored"]} == expected
        assert not errors

    @set_config(ignore=["test"])
    def test_expand_modules_with_ignore_list(self) -> None:
        """Test expand_modules with a non-default value of ignore."""
        ignore_list: list[str] = self.linter.config.ignore
        ignore_list_re = [re.compile("^\\.#")]
        path = Path(__file__).parent.parent / "regrtest_data" / "ignore_option_10669"
        modules, errors = expand_modules(
            [str(path)],
            [],
            ignore_list,
            ignore_list_re,
            [],
        )
        expected_keys = {
            str(path / "__init__.py"),
            str(path / "main.py"),
        }
        assert {k for k, v in modules.items() if not v["isignored"]} == expected_keys
        assert not errors


def _symlink_or_skip(link: Path, target: Path, target_is_directory: bool) -> None:
    """Create a symbolic link or skip the test when the platform forbids it."""
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError:
        pytest.skip("this platform does not allow creating symbolic links")


def _create_package(root: Path) -> None:
    """Create a small package: ``__init__.py``, ``a.py`` and ``sub/b.py``."""
    (root / "__init__.py").write_text("", encoding="utf8")
    (root / "a.py").write_text("value = 1\n", encoding="utf8")
    subdirectory = root / "sub"
    subdirectory.mkdir()
    (subdirectory / "__init__.py").write_text("", encoding="utf8")
    (subdirectory / "b.py").write_text("value = 2\n", encoding="utf8")


_PACKAGE_FILES = (
    "__init__.py",
    "a.py",
    os.path.join("sub", "__init__.py"),
    os.path.join("sub", "b.py"),
)


def test_expand_modules_empty_directory(tmp_path: Path) -> None:
    """An existing directory without any python file is reported as such."""
    empty = tmp_path / "empty"
    empty.mkdir()

    modules, errors = expand_modules([str(empty)], [], [], [], [])

    assert not modules
    assert [error["key"] for error in errors] == ["empty-directory"]
    assert errors[0]["args"] == (str(empty),)


def test_expand_modules_directory_with_only_non_python_files(tmp_path: Path) -> None:
    """Directories that contain no python module are also empty directories."""
    not_empty = tmp_path / "docs"
    not_empty.mkdir()
    (not_empty / "README").write_text("documentation", encoding="utf8")

    modules, errors = expand_modules([str(not_empty)], [], [], [], [])

    assert not modules
    assert [error["key"] for error in errors] == ["empty-directory"]


def test_expand_modules_explicit_file_keeps_file_semantics(tmp_path: Path) -> None:
    """A single file given explicitly is checked as a file, not a directory."""
    single_file = tmp_path / "single.py"
    single_file.write_text("value = 1\n", encoding="utf8")

    modules, errors = expand_modules([str(single_file)], [], [], [], [])

    assert list(modules) == [str(single_file)]
    assert modules[str(single_file)]["isarg"]
    assert not errors


def test_expand_modules_broken_link_as_argument(tmp_path: Path) -> None:
    """A broken symbolic link given on the command line is reported explicitly."""
    broken = tmp_path / "broken.py"
    _symlink_or_skip(broken, tmp_path / "missing.py", target_is_directory=False)

    modules, errors = expand_modules([str(broken)], [], [], [], [])

    assert not modules
    assert [error["key"] for error in errors] == ["broken-link"]
    assert errors[0]["args"] == (
        str(broken),
        "target of the symbolic link does not exist",
    )


def test_expand_modules_broken_link_inside_package(tmp_path: Path) -> None:
    """A broken link below a package is reported without blocking the package."""
    package = tmp_path / "package"
    package.mkdir()
    _create_package(package)
    _symlink_or_skip(
        package / "ghost.py",
        package / "missing.py",
        target_is_directory=False,
    )

    modules, errors = expand_modules([str(package)], [], [], [], [])

    expected = {os.path.normpath(os.path.join(str(package), f)) for f in _PACKAGE_FILES}
    assert set(modules) == expected
    assert [error["key"] for error in errors] == ["broken-link"]
    assert errors[0]["args"][0].endswith("ghost.py")


def test_expand_modules_symlink_cycle(tmp_path: Path) -> None:
    """A link pointing at an ancestor stops the traversal instead of looping."""
    package = tmp_path / "package"
    package.mkdir()
    _create_package(package)
    # package/sub/loop -> package: following it would lead back to the root.
    _symlink_or_skip(
        package / "sub" / "loop",
        package,
        target_is_directory=True,
    )

    modules, errors = expand_modules([str(package)], [], [], [], [])

    expected = {os.path.normpath(os.path.join(str(package), f)) for f in _PACKAGE_FILES}
    assert set(modules) == expected
    assert not errors


def test_expand_modules_repeated_mount_point(tmp_path: Path) -> None:
    """The same tree given through a link is only checked once."""
    package = tmp_path / "package"
    package.mkdir()
    _create_package(package)
    alias = tmp_path / "alias"
    _symlink_or_skip(alias, package, target_is_directory=True)

    modules, errors = expand_modules(
        [str(package), str(alias)], [], [], [], []
    )

    expected = {os.path.normpath(os.path.join(str(package), f)) for f in _PACKAGE_FILES}
    assert set(modules) == expected
    # Only the link-less package entry is flagged as an explicit argument.
    assert modules[os.path.normpath(str(package / "__init__.py"))]["isarg"]
    assert not any(path.startswith(str(alias)) for path in modules)
    assert not errors


def test_expand_modules_permission_error_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable subdirectory is reported but does not stop the walk."""
    package = tmp_path / "package"
    package.mkdir()
    _create_package(package)
    denied = package / "denied"
    denied.mkdir()
    (denied / "secret.py").write_text("value = 3\n", encoding="utf8")

    real_scandir = os.scandir

    def restricted_scandir(path: str, *_args: object, **_kwargs: object):
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(str(denied)):
            raise PermissionError(13, "Permission denied")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", restricted_scandir)

    modules, errors = expand_modules([str(package)], [], [], [], [])

    expected = {os.path.normpath(os.path.join(str(package), f)) for f in _PACKAGE_FILES}
    assert set(modules) == expected
    assert [error["key"] for error in errors] == ["permission-error"]
    assert errors[0]["args"][0] == os.path.normpath(str(denied))


def test_expand_modules_permission_error_does_not_block_other_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable root argument does not prevent the next one from running."""
    locked = tmp_path / "locked"
    locked.mkdir()
    single_file = tmp_path / "single.py"
    single_file.write_text("value = 1\n", encoding="utf8")

    real_scandir = os.scandir

    def restricted_scandir(path: str, *_args: object, **_kwargs: object):
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(str(locked)):
            raise PermissionError(13, "Permission denied")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", restricted_scandir)

    modules, errors = expand_modules(
        [str(locked), str(single_file)], [], [], [], []
    )

    assert [error["key"] for error in errors] == ["permission-error"]
    assert set(modules) == {os.path.normpath(str(single_file))}


class _FakeScandirResult:
    """Minimal context manager mimicking ``os.scandir`` results."""

    def __init__(self, entries: list[object]) -> None:
        self._entries = entries

    def __enter__(self) -> Iterator[object]:
        return iter(self._entries)

    def __exit__(self, *args: object) -> bool:
        return False


# pylint: disable=unused-argument
class _VanishedEntry:
    """A directory entry that disappears before it can be stat'ed."""

    def __init__(self, path: Path, name: str) -> None:
        self.path = str(path)
        self.name = name

    def is_symlink(self) -> bool:
        return False

    def is_dir(self, follow_symlinks: bool = True) -> bool:
        return False

    def stat(self, follow_symlinks: bool = True) -> os.stat_result:
        raise FileNotFoundError(2, "No such file or directory", self.path)


def test_expand_modules_file_vanishing_during_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that disappears while walking is reported as a broken link."""
    package = tmp_path / "package"
    package.mkdir()
    _create_package(package)
    gone = package / "gone.py"

    real_scandir = os.scandir

    def flaky_scandir(path: str, *_args: object, **_kwargs: object):
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(str(package)):
            entries: list[object] = list(real_scandir(path))
            entries.append(_VanishedEntry(gone, "gone.py"))
            return _FakeScandirResult(entries)
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", flaky_scandir)

    modules, errors = expand_modules([str(package)], [], [], [], [])

    expected = {os.path.normpath(os.path.join(str(package), f)) for f in _PACKAGE_FILES}
    assert set(modules) == expected
    assert [error["key"] for error in errors] == ["broken-link"]
    assert errors[0]["args"][0] == os.path.normpath(str(gone))


def test_expand_modules_independent_paths_are_isolated(tmp_path: Path) -> None:
    """A fatal discovery error and an empty dir leave the other args untouched."""
    empty = tmp_path / "empty"
    empty.mkdir()
    single_file = tmp_path / "single.py"
    single_file.write_text("value = 1\n", encoding="utf8")

    modules, errors = expand_modules(
        [
            str(empty),
            "this_pylint_module_cannot_exist_abc123",
            str(single_file),
        ],
        [],
        [],
        [],
        [],
    )

    assert {error["key"] for error in errors} == {"empty-directory", "fatal"}
    assert set(modules) == {os.path.normpath(str(single_file))}


def test_expand_modules_reports_messages_through_the_linter(tmp_path: Path) -> None:
    """The discovery errors are turned into distinct pylint messages."""
    empty = tmp_path / "empty"
    empty.mkdir()
    package = tmp_path / "package"
    package.mkdir()
    _create_package(package)

    linter = PyLinter()
    linter.set_reporter(GenericTestReporter())
    linter.check([str(empty), str(package)])

    symbols = {message.symbol for message in linter.reporter.messages}
    assert "empty-directory" in symbols
    assert {message.msg_id for message in linter.reporter.messages} <= {
        "F0012",
        "F0013",
        "F0014",
    }
