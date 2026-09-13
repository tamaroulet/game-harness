"""終了コードの正規化。パイプライン系スクリプトの `__main__` から使う。

    if __name__ == "__main__":
        sys.exit(exitcode.normalized(main))

**なぜ要るか**

`sys.exit("ABORT: ...")` は文字列を渡すので、Python は内容を表示して **rc=1** で終わる。
未捕捉の例外も rc=1。どちらも門番の REJECT（1）と区別できない。
スケジューラは「2 なら環境異常なので停止、1 なら次の Issue へ」と判断するので、
このままでは壊れた環境のまま全 Issue を不合格にして回り続ける。

判定:
  None / int 0      → 0
  int               → そのまま（0 / 1 / 2 の約束を持つ関数の戻り値を巻き込まない）
  文字列など int 以外 → 表示して 2
  未捕捉の例外       → トレースバックを表示して 2
KeyboardInterrupt は捕まえない（人間が止めたものを ABORT と記録しない）。
"""
import sys
import traceback

ABORT = 2


def normalized(fn):
    try:
        code = fn()
    except SystemExit as e:
        code = e.code
    except Exception:
        traceback.print_exc()
        return ABORT

    if code is None:
        return 0
    if isinstance(code, int) and not isinstance(code, bool):
        return code
    print(code, file=sys.stderr)
    return ABORT
