import errno
import logging
import sys
from threading import Thread

import pretend
import pytest
from mock import patch
from pip._vendor.six import PY2

from pip._internal.utils.logging import (
    BrokenStdoutLoggingError,
    ColorizedStreamHandler,
    IndentingFormatter,
    colorama,
    indent_log,
)
from pip._internal.utils.misc import captured_stderr, captured_stdout

logger = logging.getLogger(__name__)


# This is a Python 2/3 compatibility helper.
def _make_broken_pipe_error():
    """
    Return an exception object representing a broken pipe.
    """
    if PY2:
        # This is one way a broken pipe error can show up in Python 2
        # (a non-Windows example in this case).
        return IOError(errno.EPIPE, 'Broken pipe')

    return BrokenPipeError()  # noqa: F821


class TestIndentingFormatter(object):
    """Test ``pip._internal.utils.logging.IndentingFormatter``."""

    def make_record(self, msg, level_name):
        level_number = getattr(logging, level_name)
        attrs = dict(
            msg=msg,
            created=1547704837.040001,
            msecs=40,
            levelname=level_name,
            levelno=level_number,
        )
        record = logging.makeLogRecord(attrs)

        return record

    @pytest.mark.parametrize('level_name, expected', [
        ('DEBUG', 'hello\nworld'),
        ('INFO', 'hello\nworld'),
        ('WARNING', 'WARNING: hello\nworld'),
        ('ERROR', 'ERROR: hello\nworld'),
        ('CRITICAL', 'ERROR: hello\nworld'),
    ])
    def test_format(self, level_name, expected, utc):
        """
        Args:
          level_name: a logging level name (e.g. "WARNING").
        """
        record = self.make_record('hello\nworld', level_name=level_name)
        f = IndentingFormatter(fmt="%(message)s")
        assert f.format(record) == expected

    @pytest.mark.parametrize('level_name, expected', [
        ('INFO',
         '2019-01-17T06:00:37,040 hello\n'
         '2019-01-17T06:00:37,040 world'),
        ('WARNING',
         '2019-01-17T06:00:37,040 WARNING: hello\n'
         '2019-01-17T06:00:37,040 world'),
    ])
    def test_format_with_timestamp(self, level_name, expected, utc):
        record = self.make_record('hello\nworld', level_name=level_name)
        f = IndentingFormatter(fmt="%(message)s", add_timestamp=True)
        assert f.format(record) == expected

    @pytest.mark.parametrize('level_name, expected', [
        ('WARNING', 'DEPRECATION: hello\nworld'),
        ('ERROR', 'DEPRECATION: hello\nworld'),
        ('CRITICAL', 'DEPRECATION: hello\nworld'),
    ])
    def test_format_deprecated(self, level_name, expected, utc):
        """
        Test that logged deprecation warnings coming from deprecated()
        don't get another prefix.
        """
        record = self.make_record(
            'DEPRECATION: hello\nworld', level_name=level_name,
        )
        f = IndentingFormatter(fmt="%(message)s")
        assert f.format(record) == expected

    def test_thread_safety_base(self, utc):
        record = self.make_record(
            'DEPRECATION: hello\nworld', level_name='WARNING',
        )
        f = IndentingFormatter(fmt="%(message)s")
        results = []

        def thread_function():
            results.append(f.format(record))

        thread_function()
        thread = Thread(target=thread_function)
        thread.start()
        thread.join()
        assert results[0] == results[1]

    def test_thread_safety_indent_log(self, utc):
        record = self.make_record(
            'DEPRECATION: hello\nworld', level_name='WARNING',
        )
        f = IndentingFormatter(fmt="%(message)s")
        results = []

        def thread_function():
            with indent_log():
                results.append(f.format(record))

        thread_function()
        thread = Thread(target=thread_function)
        thread.start()
        thread.join()
        assert results[0] == results[1]


@pytest.fixture
def SetConsoleTextAttribute(monkeypatch):
    """Monkey-patch the SetConsoleTextAttribute function.

    This fixture records calls to the win32 function from the colorama.win32
    module. Note that colorama.win32 is an internal interface, and may change
    without notice.
    """
    from pip._vendor.colorama import win32

    wrapper = pretend.call_recorder(win32.SetConsoleTextAttribute)
    monkeypatch.setattr(win32, "SetConsoleTextAttribute", wrapper)

    return wrapper


@pytest.fixture
def empty_log_record():
    """Log record with an empty message, at WARNING level."""
    return logging.LogRecord(
        name="root",
        level=logging.WARNING,
        pathname="",
        lineno=0,
        msg="",
        args=(),
        exc_info=None,
    )


class TestColorizedStreamHandler(object):

    def _make_log_record(self):
        attrs = {
            'msg': 'my error',
        }
        record = logging.makeLogRecord(attrs)

        return record

    def test_broken_pipe_in_stderr_flush(self):
        """
        Test sys.stderr.flush() raising BrokenPipeError.

        This error should _not_ trigger an error in the logging framework.
        """
        record = self._make_log_record()

        with captured_stderr() as stderr:
            handler = ColorizedStreamHandler(stream=stderr)
            with patch('sys.stderr.flush') as mock_flush:
                mock_flush.side_effect = _make_broken_pipe_error()
                # The emit() call raises no exception.
                handler.emit(record)

            err_text = stderr.getvalue()

        assert err_text.startswith('my error')
        # Check that the logging framework tried to log the exception.
        if PY2:
            assert 'IOError: [Errno 32] Broken pipe' in err_text
            assert 'Logged from file' in err_text
        else:
            assert 'Logging error' in err_text
            assert 'BrokenPipeError' in err_text
            assert "Message: 'my error'" in err_text

    def test_broken_pipe_in_stdout_write(self):
        """
        Test sys.stdout.write() raising BrokenPipeError.

        This error _should_ trigger an error in the logging framework.
        """
        record = self._make_log_record()

        with captured_stdout() as stdout:
            handler = ColorizedStreamHandler(stream=stdout)
            with patch('sys.stdout.write') as mock_write:
                mock_write.side_effect = _make_broken_pipe_error()
                with pytest.raises(BrokenStdoutLoggingError):
                    handler.emit(record)

    def test_broken_pipe_in_stdout_flush(self):
        """
        Test sys.stdout.flush() raising BrokenPipeError.

        This error _should_ trigger an error in the logging framework.
        """
        record = self._make_log_record()

        with captured_stdout() as stdout:
            handler = ColorizedStreamHandler(stream=stdout)
            with patch('sys.stdout.flush') as mock_flush:
                mock_flush.side_effect = _make_broken_pipe_error()
                with pytest.raises(BrokenStdoutLoggingError):
                    handler.emit(record)

            output = stdout.getvalue()

        # Sanity check that the log record was written, since flush() happens
        # after write().
        assert output.startswith('my error')

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    @pytest.mark.skipif(colorama is None, reason="colorama required")
    @pytest.mark.parametrize("color", ["always", "auto"])
    def test_emit_with_color_sets_windows_console(
        self, capsys, empty_log_record, SetConsoleTextAttribute, color
    ):
        """
        It calls SetConsoleTextAttribute on Windows for colored output.
        """
        from pip._vendor.colorama import win32

        with capsys.disabled():
            if not win32.winapi_test():
                pytest.skip("output is not connected to a terminal")

            handler = ColorizedStreamHandler(color=color)
            handler.emit(empty_log_record)

        assert SetConsoleTextAttribute.calls

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    @pytest.mark.skipif(colorama is None, reason="colorama required")
    def test_emit_without_color_does_not_set_windows_console(
        self, capsys, empty_log_record, SetConsoleTextAttribute
    ):
        """
        It does not call SetConsoleTextAttribute when color is disabled.
        """
        with capsys.disabled():
            handler = ColorizedStreamHandler(color="never")
            handler.emit(empty_log_record)
        assert not SetConsoleTextAttribute.calls
