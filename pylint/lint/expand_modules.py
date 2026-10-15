# Licensed under the GPL: https://www.gnu.org/licenses/old-licenses/gpl-2.0.html
# For details: https://github.com/pylint-dev/pylint/blob/main/LICENSE
# Copyright (c) https://github.com/pylint-dev/pylint/blob/main/CONTRIBUTORS.txt

from __future__ import annotations

import os
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from re import Pattern
from typing import Literal

from astroid import modutils

from pylint.typing import ErrorDescriptionDict, ModuleDescriptionDict

# Symbols of the messages that can be emitted while discovering modules.
_ErrorKey = Literal["fatal", "permission-error", "broken-link", "empty-directory"]

# File extensions that are considered python modules. Mirrors the filtering
# done by ``astroid.modutils.get_module_files`` which we used to rely on.
_PYTHON_EXTENSIONS = (".py", ".pyi", ".so", ".pyd", ".pyw")
_INIT_FILES = ("__init__.py", "__init__.pyi")


def _modpath_from_file(filename: str, is_namespace: bool, path: list[str]) -> list[str]:
    def _is_package_cb(inner_path: str, parts: list[str]) -> bool:
        return modutils.check_modpath_has_init(inner_path, parts) or is_namespace

    return modutils.modpath_from_file_with_callback(  # type: ignore[no-any-return]
        filename, path=path, is_package_cb=_is_package_cb
    )


def discover_package_path(modulepath: str, source_roots: Sequence[str]) -> str:
    """Discover package path from one its modules and source roots."""
    dirname = os.path.realpath(os.path.expanduser(modulepath))
    if not os.path.isdir(dirname):
        dirname = os.path.dirname(dirname)

    # Look for a source root that contains the module directory
    for source_root in source_roots:
        source_root = os.path.realpath(os.path.expanduser(source_root))
        if os.path.commonpath([source_root, dirname]) in [dirname, source_root]:
            return source_root

    # Fall back to legacy discovery by looking for __init__.py upwards as
    # it's the only way given that source root was not found or was not provided
    while True:
        if not os.path.exists(os.path.join(dirname, "__init__.py")):
            return dirname
        old_dirname = dirname
        dirname = os.path.dirname(dirname)
        if old_dirname == dirname:
            return os.getcwd()


def _is_in_ignore_list_re(element: str, ignore_list_re: list[Pattern[str]]) -> bool:
    """Determines if the element is matched in a regex ignore-list."""
    return any(file_pattern.match(element) for file_pattern in ignore_list_re)


def _is_ignored_file(
    element: str,
    ignore_list: list[str],
    ignore_list_re: list[Pattern[str]],
    ignore_list_paths_re: list[Pattern[str]],
) -> bool:
    element = os.path.normpath(element)
    basename = Path(element).absolute().name
    return (
        basename in ignore_list
        or _is_in_ignore_list_re(basename, ignore_list_re)
        or _is_in_ignore_list_re(element, ignore_list_paths_re)
    )


def _is_python_file(filename: str) -> bool:
    """Return whether the given filename should be considered a python module."""
    return filename.endswith(_PYTHON_EXTENSIONS)


@dataclass
class _DiscoveryState:
    """Filesystem objects already seen during a single ``expand_modules`` call.

    Directories are followed through symbolic links but recorded by both their
    canonical real path and their ``(st_dev, st_ino)`` pair (when the filesystem
    provides reliable inode numbers), which stops recursion on cycles and on
    repeated mount points.

    Files are de-duplicated by their real path (which resolves links) and their
    inode pair (which also collapses bind mounts). Each mapping points to the
    lexical path under which the file was stored in ``result``.
    """

    visited_directories: set[str | tuple[int, int]] = field(default_factory=set)
    file_paths: dict[str, str] = field(default_factory=dict)
    file_inodes: dict[tuple[int, int], str] = field(default_factory=dict)
    # Number of python files physically seen on disk, whether or not they were
    # already scheduled through another path. Used to spot empty directories.
    files_seen: int = 0


def _real_path(path: str) -> str:
    """Canonical identity of an existing or not-yet-existing path."""
    return os.path.normcase(os.path.realpath(path))


def _inode(stat_result: os.stat_result) -> tuple[int, int] | None:
    """Return a reliable ``(device, inode)`` pair or ``None``.

    Some filesystems (e.g. some network mounts) report zero for every file, in
    which case inode based de-duplication must be disabled.
    """
    if stat_result.st_dev or stat_result.st_ino:
        return stat_result.st_dev, stat_result.st_ino
    return None


def _identity_keys(
    path: str, stat_result: os.stat_result | None = None
) -> set[str | tuple[int, int]]:
    """Return the directory identities of ``path`` (real path and inode pair)."""
    keys: set[str | tuple[int, int]] = {_real_path(path)}
    if stat_result is None:
        try:
            stat_result = os.stat(path)
        except OSError:
            return keys
    inode = _inode(stat_result)
    if inode is not None:
        keys.add(inode)
    return keys


def _already_visited(
    visited: set[str | tuple[int, int]],
    keys: set[str | tuple[int, int]],
) -> bool:
    """Return whether any identity is known and record them otherwise."""
    if any(key in visited for key in keys):
        return True
    visited.update(keys)
    return False


def _scheduled_path(
    state: _DiscoveryState, path: str
) -> tuple[str | None, os.stat_result | None]:
    """Return the lexical result key of an already scheduled physical file.

    ``None`` means the file was never seen. The stat result is returned as well
    so callers can register the inode without touching the disk again.
    """
    try:
        stat_result = os.stat(path)
    except OSError:
        stat_result = None
    existing = state.file_paths.get(_real_path(path))
    if existing is None and stat_result is not None:
        inode = _inode(stat_result)
        if inode is not None:
            existing = state.file_inodes.get(inode)
    return existing, stat_result


def _make_error(
    key: _ErrorKey,
    mod: str,
    args: tuple[object, ...] | None = None,
    ex: ImportError | SyntaxError | OSError | None = None,
) -> ErrorDescriptionDict:
    error: ErrorDescriptionDict = {"key": key, "mod": mod, "ex": ex, "args": args}
    return error


def _os_error(
    path: str, mod: str, ex: OSError
) -> ErrorDescriptionDict:
    """Build the most precise error for an ``OSError`` raised on ``path``."""
    normalized = os.path.normpath(path)
    if isinstance(ex, FileNotFoundError):
        return _make_error(
            "broken-link",
            mod,
            (normalized, ex.strerror or "no such file or directory"),
            ex,
        )
    if isinstance(ex, PermissionError):
        return _make_error(
            "permission-error",
            mod,
            (normalized, ex.strerror or "permission denied"),
            ex,
        )
    return _make_error("fatal", mod, None, ex)


def _iter_module_files(
    root_directory: str,
    ignore_list: list[str],
    list_all: bool,
    state: _DiscoveryState,
    errors: list[ErrorDescriptionDict],
    modname: str,
) -> Iterator[str]:
    """Yield python module files below ``root_directory``.

    This is a cycle-safe replacement for ``astroid.modutils.get_module_files``:

    * directories are followed through symbolic links but recorded by their
      real identity and never walked twice, which stops recursion on links
      pointing at an ancestor (or at the tree itself) and on repeated mount
      points;
    * unreadable directories, broken links and files that disappear during the
      walk are reported through ``errors`` instead of aborting the traversal of
      the remaining, independent directories.

    Physical files reached through several lexical paths (aliases, repeated
    mount points, several arguments) are all yielded here; the caller is in
    charge of scheduling each physical file only once.
    """
    stack: list[str] = [root_directory]
    while stack:
        directory = stack.pop()
        try:
            directory_stat = os.stat(directory)
        except OSError as ex:
            # The directory vanished, is a broken link or cannot be accessed.
            errors.append(_os_error(directory, modname, ex))
            continue
        if _already_visited(
            state.visited_directories, _identity_keys(directory, directory_stat)
        ):
            # Cycle (e.g. a symlink to an ancestor) or a repeated mount point.
            continue

        try:
            with os.scandir(directory) as scanner:
                entries = sorted(scanner, key=lambda entry: entry.name)
        except OSError as ex:
            errors.append(_os_error(directory, modname, ex))
            continue

        # Prune ignored names the same way astroid's ``_handle_blacklist`` does.
        entries = [entry for entry in entries if entry.name not in ignore_list]

        # A directory without an ``__init__`` file is not a package and is only
        # explored for namespace packages, matching ``get_module_files``.
        if not list_all and not any(
            entry.name in _INIT_FILES for entry in entries
        ):
            continue

        subdirectories: list[str] = []
        for entry in entries:
            entry_path = entry.path
            try:
                is_symlink = entry.is_symlink()
            except OSError as ex:
                errors.append(_os_error(entry_path, modname, ex))
                continue
            if is_symlink:
                # ``exists`` follows the link: a missing target means the link
                # is broken and must not be reported as a module to analyze.
                if not os.path.exists(entry_path):
                    errors.append(
                        _make_error(
                            "broken-link",
                            modname,
                            (
                                os.path.normpath(entry_path),
                                "target of the symbolic link does not exist",
                            ),
                        )
                    )
                    continue
            try:
                is_directory = entry.is_dir(follow_symlinks=True)
            except FileNotFoundError:
                # The entry vanished between the scan and its classification.
                errors.append(
                    _make_error(
                        "broken-link",
                        modname,
                        (
                            os.path.normpath(entry_path),
                            "file vanished while pylint was exploring the tree",
                        ),
                    )
                )
                continue
            except OSError as ex:
                errors.append(_os_error(entry_path, modname, ex))
                continue
            if is_directory:
                # Pushed in reverse so that sorted names are popped in order.
                subdirectories.append(entry_path)
            elif _is_python_file(entry.name):
                state.files_seen += 1
                try:
                    entry.stat(follow_symlinks=True)
                except OSError as ex:
                    errors.append(_os_error(entry_path, modname, ex))
                    continue
                # Directory cycle detection already prevents reaching the same
                # physical file twice within the walked trees; de-duplication of
                # files scheduled through different lexical paths (links, mount
                # points, several arguments) is done by the caller.
                yield entry_path
        stack.extend(reversed(subdirectories))


# pylint: disable = too-many-branches, too-many-locals, too-many-statements
def _expand_one(
    something: str,
    source_roots: Sequence[str],
    path: list[str],
    ignore_list: list[str],
    ignore_list_re: list[Pattern[str]],
    ignore_list_paths_re: list[Pattern[str]],
    result: dict[str, ModuleDescriptionDict],
    errors: list[ErrorDescriptionDict],
    state: _DiscoveryState,
) -> None:
    """Expand a single file, module or package argument.

    Any discovery problem is recorded in ``errors``; ``OSError`` exceptions can
    still escape, in which case the caller is expected to report them without
    touching the other, independent arguments.
    """
    basename = os.path.basename(something)
    if _is_ignored_file(
        something, ignore_list, ignore_list_re, ignore_list_paths_re
    ):
        result[something] = {
            "path": something,
            "name": "",
            "isarg": False,
            "basepath": something,
            "basename": "",
            "isignored": True,
        }
        return
    # A broken link given on the command line cannot be a module; say so
    # explicitly instead of falling back to a generic import error.
    if os.path.islink(something):
        try:
            os.stat(something)
        except FileNotFoundError:
            errors.append(
                _make_error(
                    "broken-link",
                    os.path.normpath(something),
                    (
                        os.path.normpath(something),
                        "target of the symbolic link does not exist",
                    ),
                )
            )
            return
        except PermissionError as ex:
            errors.append(_os_error(something, os.path.normpath(something), ex))
            return
        # Any other resolution error is handled with the rest below.
    module_package_path = discover_package_path(something, source_roots)
    additional_search_path = [".", module_package_path, *path]
    if os.path.exists(something):
        # this is a file or a directory
        try:
            modname = ".".join(
                modutils.modpath_from_file(something, path=additional_search_path)
            )
        except ImportError:
            modname = os.path.splitext(basename)[0]
        if os.path.isdir(something):
            filepath = os.path.join(something, "__init__.py")
        else:
            filepath = something
    else:
        # suppose it's a module or package
        modname = something
        try:
            filepath = modutils.file_from_modpath(
                modname.split("."), path=additional_search_path
            )
            if filepath is None:
                return
        except ImportError as ex:
            errors.append(_make_error("fatal", modname, None, ex))
            return
    filepath = os.path.normpath(filepath)
    modparts = (modname or something).split(".")
    try:
        spec = modutils.file_info_from_modpath(
            modparts, path=additional_search_path
        )
    except ImportError:
        # Might not be acceptable, don't crash.
        is_namespace = not os.path.exists(filepath)
        is_directory = os.path.isdir(something)
    else:
        is_namespace = modutils.is_namespace(spec)
        is_directory = modutils.is_directory(spec)
    if not is_namespace:
        default: ModuleDescriptionDict = {
            "path": filepath,
            "name": modname,
            "isarg": True,
            "basepath": filepath,
            "basename": modname,
            "isignored": False,
        }
        existing_path, root_stat = _scheduled_path(state, filepath)
        if existing_path is not None and existing_path != filepath:
            # Same physical file, already scheduled through another path (link
            # or repeated mount point): flag that entry as explicitly requested
            # instead of checking the file twice.
            result[existing_path]["isarg"] = True
        else:
            result.setdefault(filepath, default)["isarg"] = True
            state.file_paths[_real_path(filepath)] = filepath
            if root_stat is not None:
                inode = _inode(root_stat)
                if inode is not None:
                    state.file_inodes[inode] = filepath
    has_init = (
        modparts[-1] != "__init__" and os.path.basename(filepath) == "__init__.py"
    )
    if not (has_init or is_namespace or is_directory):
        return
    root_directory = os.path.dirname(filepath) or "."
    # The same physical tree may have been expanded already through another
    # argument (symlink, repeated or bind mount point, ...).
    already_visited = any(
        key in state.visited_directories
        for key in _identity_keys(root_directory)
    )
    files_found = 0
    files_seen_before = state.files_seen
    errors_before = len(errors)
    for subfilepath in _iter_module_files(
        root_directory,
        ignore_list,
        list_all=is_namespace,
        state=state,
        errors=errors,
        modname=modname,
    ):
        files_found += 1
        subfilepath = os.path.normpath(subfilepath)
        if filepath == subfilepath:
            continue
        if _is_ignored_file(
            subfilepath,
            ignore_list,
            ignore_list_re,
            ignore_list_paths_re,
        ):
            result[subfilepath] = {
                "path": subfilepath,
                "name": "",
                "isarg": False,
                "basepath": subfilepath,
                "basename": "",
                "isignored": True,
            }
            continue

        try:
            modpath = _modpath_from_file(
                subfilepath, is_namespace, path=additional_search_path
            )
            submodname = ".".join(modpath)
        except ImportError:
            submodname = os.path.splitext(os.path.basename(subfilepath))[0]
        existing_path, subfile_stat = _scheduled_path(state, subfilepath)
        if existing_path is not None and existing_path != subfilepath:
            # The physical file is already checked under another path; do not
            # add it twice. If this lexical path was given explicitly, carry
            # that flag to the existing entry.
            if subfilepath in result:
                result[existing_path]["isarg"] = True
            continue
        # Preserve arg flag if module is also explicitly given.
        isarg = subfilepath in result and result[subfilepath]["isarg"]
        result[subfilepath] = {
            "path": subfilepath,
            "name": submodname,
            "isarg": isarg,
            "basepath": filepath,
            "basename": modname,
            "isignored": False,
        }
        state.file_paths[_real_path(subfilepath)] = subfilepath
        if subfile_stat is not None:
            inode = _inode(subfile_stat)
            if inode is not None:
                state.file_inodes[inode] = subfilepath
    # An existing directory in which pylint cannot find a single python module
    # is reported as an empty directory, unless listing the tree already
    # produced a more precise error or the very same tree was explored through
    # another argument.
    tree_is_empty = state.files_seen == files_seen_before
    if (
        os.path.isdir(root_directory)
        and not already_visited
        and tree_is_empty
        and files_found == 0
        and len(errors) == errors_before
    ):
        errors.append(
            _make_error(
                "empty-directory",
                modname,
                (os.path.normpath(something),),
            )
        )


def expand_modules(
    files_or_modules: Sequence[str],
    source_roots: Sequence[str],
    ignore_list: list[str],
    ignore_list_re: list[Pattern[str]],
    ignore_list_paths_re: list[Pattern[str]],
) -> tuple[dict[str, ModuleDescriptionDict], list[ErrorDescriptionDict]]:
    """Take a list of files/modules/packages and return the list of tuple
    (file, module name) which have to be actually checked.
    """
    result: dict[str, ModuleDescriptionDict] = {}
    errors: list[ErrorDescriptionDict] = []
    state = _DiscoveryState()
    path = sys.path.copy()

    for something in files_or_modules:
        try:
            _expand_one(
                something,
                source_roots,
                path,
                ignore_list,
                ignore_list_re,
                ignore_list_paths_re,
                result,
                errors,
                state,
            )
        except OSError as ex:
            # Never let one broken path prevent the remaining arguments from
            # being expanded and checked.
            errors.append(_os_error(something, something, ex))
    return result, errors
