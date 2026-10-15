# Licensed under the GPL: https://www.gnu.org/licenses/old-licenses/gpl-2.0.html
# For details: https://github.com/pylint-dev/pylint/blob/main/LICENSE
# Copyright (c) https://github.com/pylint-dev/pylint/blob/main/CONTRIBUTORS.txt

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import pickle
import re
import sys
import tempfile
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pylint.constants import PYLINT_HOME
from pylint.utils import LinterStats

PYLINT_HOME_AS_PATH = Path(PYLINT_HOME)

#: Name of the key storing the actual ``LinterStats`` in the cache payload.
_STATS_KEY = "stats"
#: Name of the key storing the configuration fingerprint in the cache payload.
_CONFIG_HASH_KEY = "config_hash"


def _get_pdata_path(
    base_name: Path, recurs: int, pylint_home: Path = PYLINT_HOME_AS_PATH
) -> Path:
    # We strip all characters that can't be used in a filename. Also strip '/' and
    # '\\' because we want to create a single file, not sub-directories.
    underscored_name = "_".join(
        str(p.replace(":", "_").replace("/", "_").replace("\\", "_"))
        for p in base_name.parts
    )
    return pylint_home / f"{underscored_name}_{recurs}.stats"


def _stable_json_key(value: Any) -> str:
    """Return a deterministic sort key for values of heterogeneous types."""
    return f"{type(value).__name__}:{json.dumps(value, sort_keys=True, default=str)}"


def _canonicalize_config(value: Any) -> Any:
    """Convert a configuration value into JSON-serializable, deterministic data.

    Containers are normalized so that two semantically equal configurations
    produce the same representation regardless of their ordering (``set`` and
    ``dict`` ordering in particular).
    """
    if isinstance(value, re.Pattern):
        return {"__type__": "re.Pattern", "pattern": value.pattern}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _canonicalize_config(value[key])
            for key in sorted(value, key=_stable_json_key)
        }
    if isinstance(value, (set, frozenset)):
        canonical = [_canonicalize_config(item) for item in value]
        return sorted(canonical, key=_stable_json_key)
    if isinstance(value, (list, tuple)):
        return [_canonicalize_config(item) for item in value]
    if isinstance(value, bytes):
        return {"__type__": "bytes", "value": value.decode("latin1")}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # Configuration values should all be primitives or one of the types
    # handled above. ``repr`` is a stable fallback for anything exotic.
    return {"__type__": type(value).__name__, "value": repr(value)}


def get_cache_key(configuration: Mapping[str, Any]) -> str:
    """Return a stable SHA-256 fingerprint for a linter configuration.

    The fingerprint must cover everything that can change the emitted messages
    or the final rating: linter/checker options (including ``py-version`` and
    the ``evaluation`` expression) as well as linter, astroid, interpreter and
    plugin versions. Two identical configurations produce the same key while
    any change produces a different one.
    """
    canonical = json.dumps(
        _canonicalize_config(dict(configuration)),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(
        canonical.encode("utf-8"), usedforsecurity=False
    ).hexdigest()


def _discard_cache_entry(data_file: Path) -> None:
    """Remove an invalid cache entry, never raising if it cannot be removed."""
    with contextlib.suppress(OSError):
        data_file.unlink()


def load_results(
    base: str | Path,
    pylint_home: str | Path = PYLINT_HOME,
    config_hash: str | None = None,
) -> LinterStats | None:
    """Load previously cached results.

    ``config_hash`` is the fingerprint returned by :func:`get_cache_key` for the
    current run. When it is provided, an entry stored with a different
    fingerprint (the rule configuration, plugins or target interpreter changed)
    is not reused and is removed. A corrupted entry likewise leads to a cache
    miss (the file is then analyzed again) and gets cleaned up.
    """
    base = Path(base)
    pylint_home = Path(pylint_home)
    data_file = _get_pdata_path(base, 1, pylint_home)

    if not data_file.exists():
        return None

    try:
        with open(data_file, "rb") as stream:
            data = pickle.load(stream)
    except Exception:  # pylint: disable=broad-except
        # There's an issue with the cache (truncated or partially written file,
        # incompatible pickle, ...): we analyze again and remove the bad entry
        # so it cannot be read on the next run either.
        _discard_cache_entry(data_file)
        return None

    if isinstance(data, dict) and _STATS_KEY in data and _CONFIG_HASH_KEY in data:
        cached_hash = data[_CONFIG_HASH_KEY]
        if config_hash is not None and cached_hash != config_hash:
            # The configuration that influences messages/rating changed, the
            # old result can no longer be reused.
            _discard_cache_entry(data_file)
            return None
        stats = data[_STATS_KEY]
    elif isinstance(data, LinterStats):
        # Cache written by a pylint version that did not store a fingerprint.
        # It can only be reused when no fingerprint has to be verified.
        if config_hash is not None:
            _discard_cache_entry(data_file)
            return None
        stats = data
    else:
        stats = data

    if not isinstance(stats, LinterStats):
        warnings.warn(
            "You're using an old pylint cache with invalid data following "
            f"an upgrade, please delete '{data_file}'.",
            UserWarning,
            stacklevel=2,
        )
        _discard_cache_entry(data_file)
        return None
    return stats


def save_results(
    results: LinterStats,
    base: str | Path,
    pylint_home: str | Path = PYLINT_HOME,
    config_hash: str | None = None,
) -> None:
    """Cache the results of a run together with its configuration fingerprint.

    The entry is written to a temporary file in the same directory and then
    moved into place atomically, so concurrent (e.g. parallel) runs can never
    observe a half-written entry.
    """
    base = Path(base)
    pylint_home = Path(pylint_home)
    try:
        pylint_home.mkdir(parents=True, exist_ok=True)
    except OSError:  # pragma: no cover
        print(f"Unable to create directory {pylint_home}", file=sys.stderr)
        return
    data_file = _get_pdata_path(base, 1, pylint_home)
    payload = {_CONFIG_HASH_KEY: config_hash, _STATS_KEY: results}
    temporary_name: str | None = None
    try:
        try:
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{data_file.name}.", suffix=".tmp", dir=pylint_home
            )
            with os.fdopen(fd, "wb") as stream:
                pickle.dump(payload, stream)
            os.replace(temporary_name, data_file)
            temporary_name = None
        finally:
            if temporary_name is not None:
                with contextlib.suppress(OSError):
                    os.unlink(temporary_name)
    except OSError as ex:  # pragma: no cover
        print(f"Unable to create file {data_file}: {ex}", file=sys.stderr)
