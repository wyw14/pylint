# Licensed under the GPL: https://www.gnu.org/licenses/old-licenses/gpl-2.0.html
# For details: https://github.com/pylint-dev/pylint/blob/main/LICENSE
# Copyright (c) https://github.com/pylint-dev/pylint/blob/main/CONTRIBUTORS.txt

from __future__ import annotations

import os
from collections.abc import Callable
from copy import copy
from typing import TYPE_CHECKING, TextIO

from pylint.message import Message
from pylint.reporters.base_reporter import BaseReporter
from pylint.utils import LinterStats

if TYPE_CHECKING:
    from pylint.lint import PyLinter
    from pylint.reporters.ureports.nodes import Section


class MultiReporter:
    """Reports messages and layouts in plain text."""

    name = "_internal_multi_reporter"
    # Note: do not register this reporter with linter.register_reporter as it is
    #       not intended to be used directly like a regular reporter, but is
    #       instead used to implement the
    #       `--output-format=json:somefile.json,colorized`
    #       multiple output formats feature

    extension = ""

    def __init__(
        self,
        sub_reporters: list[BaseReporter],
        close_output_files: Callable[[], None],
        output: TextIO | None = None,
    ):
        self._sub_reporters = sub_reporters
        self.close_output_files = close_output_files
        self._path_strip_prefix = os.getcwd() + os.sep
        self._linter: PyLinter | None = None
        self.out = output
        self.messages: list[Message] = []

    @property
    def out(self) -> TextIO | None:
        return self.__out

    @out.setter
    def out(self, output: TextIO | None = None) -> None:
        """MultiReporter doesn't have its own output.

        This method is only provided for API parity with BaseReporter
        and should not be called with non-None values for 'output'.
        """
        self.__out = None
        if output is not None:
            raise NotImplementedError("MultiReporter does not support direct output.")

    def __del__(self) -> None:
        self.close_output_files()

    @property
    def path_strip_prefix(self) -> str:
        return self._path_strip_prefix

    @property
    def linter(self) -> PyLinter | None:
        return self._linter

    @linter.setter
    def linter(self, value: PyLinter) -> None:
        self._linter = value
        for rep in self._sub_reporters:
            rep.linter = value

    def handle_message(self, msg: Message) -> None:
        """Handle a new message triggered on the current file."""
        # Keep a pristine copy of the message set so the results can be
        # re-output with another reporter later.
        self.messages.append(copy(msg))
        for rep in self._sub_reporters:
            # We provide a copy so reporters can't modify message for others.
            self._call_sub_reporter(rep, rep.handle_message, copy(msg))

    @staticmethod
    def _call_sub_reporter(
        reporter: BaseReporter, method: Callable[..., None], *args: object
    ) -> None:
        """Call a sub-reporter without letting its failures affect the others.

        A failure is recorded on the sub-reporter itself, which reports it
        exactly once and closes its own resources.
        """
        try:
            method(*args)
        except Exception as ex:  # pylint: disable=broad-except
            reporter._report_reporter_error(ex)

    def writeln(self, string: str = "") -> None:
        """Write a line in the output buffer."""
        for rep in self._sub_reporters:
            self._call_sub_reporter(rep, rep.writeln, string)

    def display_reports(self, layout: Section) -> None:
        """Display results encapsulated in the layout tree."""
        for rep in self._sub_reporters:
            self._call_sub_reporter(rep, rep.display_reports, layout)

    def display_messages(self, layout: Section | None) -> None:
        """Hook for displaying the messages of the reporter."""
        for rep in self._sub_reporters:
            self._call_sub_reporter(rep, rep.display_messages, layout)

    def on_set_current_module(self, module: str, filepath: str | None) -> None:
        """Hook called when a module starts to be analysed."""
        for rep in self._sub_reporters:
            self._call_sub_reporter(rep, rep.on_set_current_module, module, filepath)

    def on_close(
        self,
        stats: LinterStats,
        previous_stats: LinterStats | None,
    ) -> None:
        """Hook called when a module finished analyzing."""
        for rep in self._sub_reporters:
            self._call_sub_reporter(rep, rep.on_close, stats, previous_stats)

    def disable(self) -> None:
        """Disable all sub-reporters without reporting an error."""
        for rep in self._sub_reporters:
            rep.disable()
