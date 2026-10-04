"""テストの標準出力・標準エラーを、合格したときだけ捨てる助け手。`class X(quiet.Quiet, unittest.TestCase)` の形で使う。"""
import contextlib
import io
import sys


class Quiet:
    """unittest.TestCase と併せて使う mixin。テスト 1 件の間だけ標準出力・標準エラーを緩衝へ向ける。"""

    _quiet_buf = None

    def run(self, result=None):
        old_out, real_err = sys.stdout, sys.stderr
        buf = io.StringIO()
        self._quiet_buf = buf
        before = self._bad_count(result)
        sys.stdout = sys.stderr = buf
        try:
            outcome = super().run(result)
        finally:
            sys.stdout, sys.stderr = old_out, real_err
        final = result if result is not None else outcome
        if self._bad_count(final) > before:
            real_err.write(buf.getvalue())
            real_err.flush()
        return outcome

    def written(self):
        return self._quiet_buf.getvalue() if self._quiet_buf is not None else ""

    @staticmethod
    def _bad_count(result):
        return 0 if result is None else len(result.failures) + len(result.errors)


def captured(fn, *args, **kwargs):
    """fn を呼び、(戻り値, その間に標準出力と標準エラーへ書かれた文字列) を返す。例外はそのまま伝える。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        value = fn(*args, **kwargs)
    return value, buf.getvalue()
