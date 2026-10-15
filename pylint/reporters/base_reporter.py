# Licensed under the GPL: https://www.gnu.org/licenses/old-licenses/gpl-2.0.html
# For details: https://github.com/pylint-dev/pylint/blob/main/LICENSE
# Copyright (c) https://github.com/pylint-dev/pylint/blob/main/CONTRIBUTORS.txt

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, TextIO

from pylint.message import Message
from pylint.reporters.ureports.nodes import Text
from pylint.utils import LinterStats

if TYPE_CHECKING:
    from pylint.lint.pylinter import PyLinter
    from pylint.reporters.ureports.nodes import Section


class BaseReporter:
    """Base class for reporters.

    symbols: show short symbolic names for messages.
    """

    extension = ""

    name = "base"
    """Name of the reporter."""

    def __init__(self, output: TextIO | None = None) -> None:
        self.linter: PyLinter
        self.section = 0
        self.out: TextIO = output or sys.stdout
        self.messages: list[Message] = []
        # Build the path prefix to strip to get relative paths
        self.path_strip_prefix = os.getcwd() + os.sep
        # Whether the reporter owns ``self.out`` (and has to close it).
        # sys.stdout and streams provided by the caller are never owned.
        self._owns_output = False
        # Whether the reporter already reported an output failure. A failure
        # is reported exactly once and silences the reporter afterwards.
        self._reporter_failed = False
        # Whether output was explicitly disabled (without a failure).
        self._output_disabled = False

    def handle_message(self, msg: Message) -> None:
        """Handle a new message triggered on the current file."""
        self.messages.append(msg)

    def writeln(self, string: str = "") -> None:
        """Write a line in the output buffer."""
        if self._reporter_failed or self._output_disabled:
            return
        try:
            print(string, file=self.out)
        except UnicodeEncodeError as ex:
            # The output codec cannot represent some characters: replace them
            # so the rest of the report can still be emitted. The failing
            # codec name is carried by the exception itself.
            encoding = getattr(self.out, "encoding", None) or ex.encoding or "utf-8"
            safe_string = string.encode(encoding, errors="replace").decode(encoding)
            try:
                print(safe_string, file=self.out)
            except (UnicodeEncodeError, OSError) as new_ex:
                self._report_reporter_error(new_ex)
        except OSError as ex:
            self._report_reporter_error(ex)

    @staticmethod
    def reencode_output_after_unicode_error(string: str) -> str:
        return string.encode(encoding="utf-8", errors="replace").decode("utf8")

    def _report_reporter_error(self, error: Exception) -> None:
        """Report an output failure once, then close and silence the reporter.

        Encoding, writing or formatting failures never propagate: the core
        diagnostics and the exit-code decision must always be completed first.
        """
        if self._reporter_failed or self._output_disabled:
            return
        self._reporter_failed = True
        print(
            f"Error: reporter '{self.name}' failed to write its output: {error}",
            file=sys.stderr,
        )
        self.close()

    def disable(self) -> None:
        """Disable the reporter's output without reporting an error.

        Messages are still collected, so the results can be re-output later
        with a different reporter.
        """
        self._output_disabled = True
        self.close()

    def close(self) -> None:
        """Close the output resource owned by the reporter.

        Streams provided by the caller and sys.stdout are never closed.
        """
        if not self._owns_output:
            return
        try:
            self.out.close()
        except OSError:
            pass

    def display_reports(self, layout: Section) -> None:
        """Display results encapsulated in the layout tree."""
        self.section = 0
        if self._reporter_failed or self._output_disabled:
            return
        try:
            if layout.report_id:
                if isinstance(layout.children[0].children[0], Text):
                    layout.children[0].children[0].data += f" ({layout.report_id})"
                else:
                    raise ValueError(f"Incorrect child for {layout.children[0].children}")
            self._display(layout)
        except (OSError, UnicodeError, ValueError, IndexError) as ex:
            self._report_reporter_error(ex)

    def _display(self, layout: Section) -> None:
        """Display the layout."""
        raise NotImplementedError()

    def display_messages(self, layout: Section | None) -> None:
        """Hook for displaying the messages of the reporter.

        This will be called whenever the underlying messages
        needs to be displayed. For some reporters, it probably
        doesn't make sense to display messages as soon as they
        are available, so some mechanism of storing them could be used.
        This method can be implemented to display them after they've
        been aggregated.
        """

    # Event callbacks

    def on_set_current_module(self, module: str, filepath: str | None) -> None:
        """Hook called when a module starts to be analysed."""

    def on_close(
        self,
        stats: LinterStats,
        previous_stats: LinterStats | None,
    ) -> None:
        """Hook called when a module finished analyzing."""
