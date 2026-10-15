# Licensed under the GPL: https://www.gnu.org/licenses/old-licenses/gpl-2.0.html
# For details: https://github.com/pylint-dev/pylint/blob/main/LICENSE
# Copyright (c) https://github.com/pylint-dev/pylint/blob/main/CONTRIBUTORS.txt

# pylint: disable=redefined-outer-name

from __future__ import annotations

import codecs
import os
import warnings
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from json import dumps
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

import pytest

from pylint import checkers
from pylint.checkers import BaseChecker
from pylint.interfaces import HIGH
from pylint.lint import PyLinter
from pylint.message.message import Message
from pylint.reporters import BaseReporter, CollectingReporter, MultiReporter
from pylint.reporters.text import ParseableTextReporter, TextReporter
from pylint.reporters.ureports import nodes as report_nodes
from pylint.testutils._run import _Run as Run
from pylint.typing import FileItem, MessageLocationTuple

if TYPE_CHECKING:
    from pylint.reporters.ureports.nodes import Section


@pytest.fixture(scope="module")
def reporter() -> type[TextReporter]:
    return TextReporter


@pytest.fixture(scope="module")
def disable() -> list[str]:
    return ["I"]


def test_template_option(linter: PyLinter) -> None:
    output = StringIO()
    linter.reporter.out = output
    linter.config.msg_template = "{msg_id}:{line:03d}"
    linter.open()
    linter.set_current_module("0123")
    linter.add_message("C0301", line=1, args=(1, 2))
    linter.add_message("line-too-long", line=2, args=(3, 4))
    assert output.getvalue() == "************* Module 0123\nC0301:001\nC0301:002\n"


def test_template_option_default(linter: PyLinter) -> None:
    """Test the default msg-template setting."""
    output = StringIO()
    linter.reporter.out = output
    linter.open()
    linter.set_current_module("my_module")
    linter.add_message("C0301", line=1, args=(1, 2))
    linter.add_message("line-too-long", line=2, args=(3, 4))

    out_lines = output.getvalue().split("\n")
    assert out_lines[1] == "my_module:1:0: C0301: Line too long (1/2) (line-too-long)"
    assert out_lines[2] == "my_module:2:0: C0301: Line too long (3/4) (line-too-long)"


def test_template_option_end_line(linter: PyLinter) -> None:
    """Test the msg-template option with end_line and end_column."""
    output = StringIO()
    linter.reporter.out = output
    linter.config.msg_template = (
        "{path}:{line}:{column}:{end_line}:{end_column}: {msg_id}: {msg} ({symbol})"
    )
    linter.open()
    linter.set_current_module("my_mod")
    linter.add_message("C0301", line=1, args=(1, 2))
    linter.add_message(
        "line-too-long", line=2, end_lineno=2, end_col_offset=4, args=(3, 4)
    )

    out_lines = output.getvalue().split("\n")
    assert out_lines[1] == "my_mod:1:0::: C0301: Line too long (1/2) (line-too-long)"
    assert out_lines[2] == "my_mod:2:0:2:4: C0301: Line too long (3/4) (line-too-long)"


def test_template_option_non_existing(linter: PyLinter) -> None:
    """Test the msg-template option with non-existent options.
    This makes sure that this option remains backwards compatible as new
    parameters do not break on previous versions.
    """
    output = StringIO()
    linter.reporter.out = output
    linter.config.msg_template = "{path}:{line}:{categ}:({a_second_new_option:03d})"
    linter.open()
    with pytest.warns(UserWarning) as records:
        linter.set_current_module("my_mod")
        assert len(records) == 2
        assert "Don't recognize the argument 'categ'" in records[0].message.args[0]
    assert (
        "Don't recognize the argument 'a_second_new_option'"
        in records[1].message.args[0]
    )

    linter.add_message("C0301", line=1, args=(1, 2))
    linter.add_message(
        "line-too-long", line=2, end_lineno=2, end_col_offset=4, args=(3, 4)
    )

    out_lines = output.getvalue().split("\n")
    assert out_lines[1] == "my_mod:1::()"
    assert out_lines[2] == "my_mod:2::()"


def test_template_option_with_header(linter: PyLinter) -> None:
    output = StringIO()
    linter.reporter.out = output
    linter.config.msg_template = '{{ "Category": "{category}" }}'
    linter.open()
    linter.set_current_module("my_mod")

    linter.add_message("C0301", line=1, args=(1, 2))
    linter.add_message(
        "line-too-long", line=2, end_lineno=2, end_col_offset=4, args=(3, 4)
    )

    out_lines = output.getvalue().split("\n")
    assert out_lines[1] == '{ "Category": "convention" }'
    assert out_lines[2] == '{ "Category": "convention" }'


def test_parseable_output_deprecated() -> None:
    with warnings.catch_warnings(record=True) as cm:
        warnings.simplefilter("always")
        ParseableTextReporter()

    assert len(cm) == 1
    assert isinstance(cm[0].message, DeprecationWarning)


def test_parseable_output_regression() -> None:
    output = StringIO()
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("ignore", category=DeprecationWarning)
        linter = PyLinter(reporter=ParseableTextReporter())

    checkers.initialize(linter)
    linter.config.persistent = 0
    linter.reporter.out = output
    linter.set_option("output-format", "parseable")
    linter.open()
    linter.set_current_module("0123")
    linter.add_message("line-too-long", line=1, args=(1, 2))
    assert (
        output.getvalue() == "************* Module 0123\n"
        "0123:1: [C0301(line-too-long), ] "
        "Line too long (1/2)\n"
    )


class NopReporter(BaseReporter):
    name = "nop-reporter"
    extension = ""

    def __init__(self, output: TextIO | None = None) -> None:
        super().__init__(output)
        print("A NopReporter was initialized.", file=self.out)

    def writeln(self, string: str = "") -> None:
        pass

    def _display(self, layout: Section) -> None:
        pass


def test_multi_format_output(tmp_path: Path) -> None:
    text = StringIO(newline=None)
    json = tmp_path / "somefile.json"

    source_file = tmp_path / "somemodule.py"
    source_file.write_text('NOT_EMPTY = "This module is not empty"\n')
    dumps(str(source_file))

    nop_format = NopReporter.__module__ + "." + NopReporter.__name__
    formats = ",".join(["json2:" + str(json), "text", nop_format])

    with redirect_stdout(text):
        linter = PyLinter()
        linter.load_default_plugins()
        linter.set_option("persistent", False)
        linter.set_option("reports", True)
        linter.set_option("score", True)
        linter.set_option("score", True)
        linter.set_option("output-format", formats)

        assert linter.reporter.linter is linter
        with pytest.raises(NotImplementedError):
            linter.reporter.out = text

        linter.open()
        linter.check_single_file_item(
            FileItem("somemodule", str(source_file), "somemodule")
        )
        linter.add_message("line-too-long", line=1, args=(1, 2))
        linter.generate_reports()
        linter.reporter.writeln("direct output")

        # Ensure the output files are flushed and closed
        assert isinstance(linter.reporter, MultiReporter)
        linter.reporter.close_output_files()
        del linter.reporter

    with open(json, encoding="utf-8") as f:
        assert '"messageId": "C0114"' in f.read()

    assert (
        text.getvalue() == "A NopReporter was initialized.\n"
        "************* Module somemodule\n"
        f"{source_file}:1:0: C0114: Missing module docstring (missing-module-docstring)\n"
        f"{source_file}:1:0: C0301: Line too long (1/2) (line-too-long)\n"
        "\n"
        "\n"
        "Report\n"
        "======\n"
        "1 statements analysed.\n"
        "\n"
        "Statistics by type\n"
        "------------------\n"
        "\n"
        "+---------+-------+-----------+-----------+------------+---------+\n"
        "|type     |number |old number |difference |%documented |%badname |\n"
        "+=========+=======+===========+===========+============+=========+\n"
        "|module   |1      |NC         |NC         |0.00        |0.00     |\n"
        "+---------+-------+-----------+-----------+------------+---------+\n"
        "|class    |0      |NC         |NC         |0           |0        |\n"
        "+---------+-------+-----------+-----------+------------+---------+\n"
        "|method   |0      |NC         |NC         |0           |0        |\n"
        "+---------+-------+-----------+-----------+------------+---------+\n"
        "|function |0      |NC         |NC         |0           |0        |\n"
        "+---------+-------+-----------+-----------+------------+---------+\n"
        "\n"
        "\n"
        "\n"
        "3 lines have been analyzed\n"
        "\n"
        "Raw metrics\n"
        "-----------\n"
        "\n"
        "+----------+-------+------+---------+-----------+\n"
        "|type      |number |%     |previous |difference |\n"
        "+==========+=======+======+=========+===========+\n"
        "|code      |2      |66.67 |NC       |NC         |\n"
        "+----------+-------+------+---------+-----------+\n"
        "|docstring |0      |0.00  |NC       |NC         |\n"
        "+----------+-------+------+---------+-----------+\n"
        "|comment   |0      |0.00  |NC       |NC         |\n"
        "+----------+-------+------+---------+-----------+\n"
        "|empty     |1      |33.33 |NC       |NC         |\n"
        "+----------+-------+------+---------+-----------+\n"
        "\n"
        "\n"
        "\n"
        "Duplication\n"
        "-----------\n"
        "\n"
        "+-------------------------+------+---------+-----------+\n"
        "|                         |now   |previous |difference |\n"
        "+=========================+======+=========+===========+\n"
        "|nb duplicated lines      |0     |NC       |NC         |\n"
        "+-------------------------+------+---------+-----------+\n"
        "|percent duplicated lines |0.000 |NC       |NC         |\n"
        "+-------------------------+------+---------+-----------+\n"
        "\n"
        "\n"
        "\n"
        "Messages by category\n"
        "--------------------\n"
        "\n"
        "+-----------+-------+---------+-----------+\n"
        "|type       |number |previous |difference |\n"
        "+===========+=======+=========+===========+\n"
        "|convention |2      |NC       |NC         |\n"
        "+-----------+-------+---------+-----------+\n"
        "|refactor   |0      |NC       |NC         |\n"
        "+-----------+-------+---------+-----------+\n"
        "|warning    |0      |NC       |NC         |\n"
        "+-----------+-------+---------+-----------+\n"
        "|error      |0      |NC       |NC         |\n"
        "+-----------+-------+---------+-----------+\n"
        "\n"
        "\n"
        "\n"
        "Messages\n"
        "--------\n"
        "\n"
        "+-------------------------+------------+\n"
        "|message id               |occurrences |\n"
        "+=========================+============+\n"
        "|missing-module-docstring |1           |\n"
        "+-------------------------+------------+\n"
        "|line-too-long            |1           |\n"
        "+-------------------------+------------+\n"
        "\n"
        "\n"
        "\n"
        "\n"
        "-----------------------------------\n"
        "Your code has been rated at 0.00/10\n"
        "\n"
        "direct output\n"
    )


def test_multi_reporter_independant_messages() -> None:
    """Messages should not be modified by multiple reporters."""
    check_message = "Not modified"

    class ReporterModify(BaseReporter):
        def handle_message(self, msg: Message) -> None:
            msg.msg = "Modified message"

        def writeln(self, string: str = "") -> None:
            pass

        def _display(self, layout: Section) -> None:
            pass

    class ReporterCheck(BaseReporter):
        def handle_message(self, msg: Message) -> None:
            assert (
                msg.msg == check_message
            ), "Message object should not be changed by other reporters."

        def writeln(self, string: str = "") -> None:
            pass

        def _display(self, layout: Section) -> None:
            pass

    multi_reporter = MultiReporter([ReporterModify(), ReporterCheck()], lambda: None)

    message = Message(
        symbol="missing-docstring",
        msg_id="C0123",
        location=MessageLocationTuple("abspath", "path", "module", "obj", 1, 2, 1, 3),
        msg=check_message,
        confidence=HIGH,
    )

    multi_reporter.handle_message(message)

    assert (
        message.msg == check_message
    ), "Message object should not be changed by reporters."


def test_display_results_is_renamed() -> None:
    class CustomReporter(TextReporter):
        def _display(self, layout: Section) -> None:
            return None

    reporter = CustomReporter()
    with pytest.raises(AttributeError) as exc:
        # pylint: disable=no-member
        reporter.display_results()  # type: ignore[attr-defined]
    assert "no attribute 'display_results'" in str(exc)


# Reporter failure handling #################################################


class FailingStream:
    """A text output stream that fails whenever something is written to it."""

    def __init__(self, error: Exception, encoding: str = "utf-8") -> None:
        self.error = error
        self.encoding = encoding
        self.closed = False

    def write(self, _: str) -> int:
        raise self.error

    def flush(self) -> None:
        raise self.error

    def close(self) -> None:
        self.closed = True


def test_reporter_write_failure_is_reported_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A write failure is reported once and silences further output."""
    stream = FailingStream(OSError("disk full"))
    reporter = TextReporter(stream)

    reporter.writeln("first line")
    # Later output attempts stay silent and don't report the failure again.
    reporter.writeln("second line")
    reporter.display_reports(report_nodes.Section())

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("reporter 'text' failed to write its output") == 1
    assert "disk full" in captured.err
    # A stream provided by the caller is not owned, so the reporter must not
    # close it itself.
    assert not stream.closed


def test_reporter_failure_closes_owned_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When the reporter owns its output, it closes it upon failure."""
    stream = FailingStream(OSError("disk full"))
    reporter = TextReporter(stream)
    # Ownership is set when a file is opened for the reporter itself
    # (see PyLinter._load_reporters).
    reporter._owns_output = True

    reporter.writeln("first line")

    assert stream.closed
    captured = capsys.readouterr()
    assert "reporter 'text' failed" in captured.err


def test_reporter_unicode_error_replaces_characters(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Characters the output codec can't encode are replaced, not fatal."""
    buffer = BytesIO()
    stream = codecs.getwriter("ascii")(buffer)
    reporter = TextReporter(stream)

    reporter.writeln("café")

    assert buffer.getvalue() == b"caf?\n"
    assert capsys.readouterr().err == ""


def test_reporter_unicode_error_failure_when_retry_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If writing with replacement also fails, the failure is reported once."""
    stream = FailingStream(UnicodeEncodeError("utf-8", "é", 0, 1, "boom"))
    reporter = TextReporter(stream)

    reporter.writeln("café")
    reporter.writeln("more")

    captured = capsys.readouterr()
    assert captured.err.count("reporter 'text' failed") == 1


def test_broken_msg_template_is_a_reporter_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Formatting failures while rendering a message are contained."""
    stream = StringIO()
    reporter = TextReporter(stream)
    reporter._fixed_template = "{unknown_arg}"

    message = Message(
        msg_id="C0301",
        symbol="line-too-long",
        msg="Line too long",
        location=MessageLocationTuple("abspath", "path", "module", "obj", 1, 2, 1, 3),
        confidence=HIGH,
    )
    reporter.write_message(message)

    captured = capsys.readouterr()
    assert "reporter 'text' failed" in captured.err
    assert stream.getvalue() == ""


@pytest.fixture()
def pop_pylintrc() -> None:
    os.environ.pop("PYLINTRC", None)


def _write_lintable_module(tmp_path: Path) -> Path:
    source = tmp_path / "mod.py"
    source.write_text("X = 1\n" + "#" * 101 + "\n", encoding="utf-8")
    return source


def test_check_completes_when_reporter_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Core diagnostics and the exit decision survive a reporter failure."""
    source = _write_lintable_module(tmp_path)

    linter = PyLinter()
    linter.load_default_plugins()
    linter.config.persistent = False
    stream = FailingStream(OSError("disk full"))
    linter.set_reporter(TextReporter(stream))
    linter.open()

    linter.check([str(source)])
    score_value = linter.generate_reports()

    # All diagnostics were still collected and counted.
    assert len(linter.reporter.messages) == 2
    assert linter.msg_status != 0
    # The score is still evaluated even though it could not be printed.
    assert score_value == 0.0

    # Many output attempts happen (module header, messages, score section),
    # but the failure is reported exactly once.
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("reporter 'text' failed") == 1


class ModuleVisitCounter(BaseChecker):
    """Checker counting how many times its checks were actually executed."""

    name = "module-visit-counter"
    # The checker needs at least one (enabled) message, otherwise
    # prepare_checkers prunes it and visit_module is never dispatched.
    msgs = {
        "C9999": (
            "A module was visited by the counter",
            "counter-module-visited",
            "Internal message of ModuleVisitCounter, never emitted.",
        )
    }

    def __init__(self, linter: PyLinter) -> None:
        super().__init__(linter)
        self.visits = 0

    def visit_module(self, node: object) -> None:
        self.visits += 1


def test_re_output_with_another_reporter(tmp_path: Path) -> None:
    """Re-output renders the same messages without re-running any checks."""
    source = _write_lintable_module(tmp_path)

    linter = PyLinter()
    linter.load_default_plugins()
    counter = ModuleVisitCounter(linter)
    linter.register_checker(counter)
    linter.config.persistent = False
    linter.set_reporter(TextReporter(FailingStream(OSError("broken"))))
    linter.open()
    linter.check([str(source)])

    failed_reporter = linter.reporter
    snapshot = list(failed_reporter.messages)
    assert counter.visits == 1

    new_output = StringIO()
    linter.re_output(TextReporter(new_output))

    # Checks were not executed again ...
    assert counter.visits == 1
    # ... and the original message set is unchanged.
    assert failed_reporter.messages == snapshot
    # The replacement reporter renders the full, identical message set.
    rendered = new_output.getvalue()
    assert rendered.count("************* Module mod") == 1
    assert "C0114" in rendered
    assert "C0301" in rendered
    assert len(linter.reporter.messages) == 2


class MutatingReporter(BaseReporter):
    """Reporter that mutates every message it receives."""

    name = "mutating-reporter"

    def handle_message(self, msg: Message) -> None:
        self.messages.append(msg)
        msg.msg = "MUTATED"

    def writeln(self, string: str = "") -> None:
        pass

    def _display(self, layout: Section) -> None:
        pass


def test_re_output_does_not_modify_original_messages(tmp_path: Path) -> None:
    """The new reporter works on copies and cannot mutate the stored set."""
    source = _write_lintable_module(tmp_path)

    linter = PyLinter()
    linter.load_default_plugins()
    linter.config.persistent = False
    collector = CollectingReporter()
    linter.set_reporter(collector)
    linter.open()
    linter.check([str(source)])

    original_texts = [m.msg for m in collector.messages]

    linter.re_output(MutatingReporter())

    # The new reporter works on copies: the original message objects (and
    # therefore the message set) cannot be modified by re-output.
    assert [m.msg for m in collector.messages] == original_texts


@pytest.mark.usefixtures("pop_pylintrc")
def test_stdout_file_disabled_outputs_are_distinguishable(tmp_path: Path) -> None:
    """stdout output, file output and a disabled reporter differ clearly."""
    source = _write_lintable_module(tmp_path)

    # 1. Standard output: the messages are written to stdout.
    stdout = StringIO()
    with redirect_stdout(stdout):
        Run([str(source)], exit=False)
    assert "C0114" in stdout.getvalue()

    # 2. File output: the messages land in the file, nothing on stdout.
    report_file = tmp_path / "report.txt"
    file_stdout = StringIO()
    with redirect_stdout(file_stdout):
        Run(["--output", str(report_file), str(source)], exit=False)
    assert "C0114" in report_file.read_text(encoding="utf-8")
    assert file_stdout.getvalue() == ""

    # 3. Disabled reporter: no output anywhere, but messages are collected.
    disabled_stdout = StringIO()
    with redirect_stdout(disabled_stdout):
        linter = PyLinter()
        linter.load_default_plugins()
        linter.config.persistent = False
        linter.open()
        linter.reporter.disable()
        linter.check([str(source)])
        linter.generate_reports()
    assert disabled_stdout.getvalue() == ""
    assert len(linter.reporter.messages) == 2
