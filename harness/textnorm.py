"""文字列の改行を LF にそろえる。

    textnorm.normalize_newlines("a\\r\\nb\\rc\\n") == "a\\nb\\nc\\n"

CRLF を先に LF にしてから、残った単独の CR を LF にする（順序を逆にすると CRLF が 2 つの改行になる）。
"""


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")
