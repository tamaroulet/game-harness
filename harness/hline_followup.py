"""H ライン（harness/hline.py）の、前の差分から続ける試行（2 回目から・再開・延長）に渡す失敗の出力の絞り込み。
最初の試行に渡す出力は変えない。"""
import re

import box_tree

HEAD_LINES = 5   # 落ちた試験の前に残す見出しの行数の上限

# 落ちた試験の名前の行（FAIL:・ERROR:）と、トレースバックの塊。gate_order._FAILURE と同じ形に、
# 「次の FAIL:・ERROR: の行の前でも切る」を足したもの。Gate 1 が返す出力（gate_order.report）は、区切り線を除いて
# 塊をつないだ digest なので、区切り線だけで切ると最初のトレースバックが文末まで飲み込み、2 件目からの落ちが
# 続きの試行に届かなかった（11-05、提案役の 3 回目が見つけ、会話の席が再現した）。
_BLOCK = re.compile(r"^(?:FAIL|ERROR): [^\n]*"
                    r"|^Traceback \(most recent call last\):.*?(?=^[=-]{20,}[ \t\r]*$|^(?:FAIL|ERROR): |\Z)",
                    re.MULTILINE | re.DOTALL)


def narrow(feedback, limit):
    """失敗の出力を絞る（_failures）。受け取らなかったファイルの知らせ（箱の走りで run_task が後ろに足す。長さは box_tree.dropped_note が抑える）は
    絞らずに残す。消えると、実装役には無いファイルの理由が分からない（11-08 の空走りで、2 回目の入力に届いていないのが分かった）。"""
    text, head, note = (feedback or "").partition(box_tree.DROPPED_HEAD)
    body = _failures(text, limit)
    return f"{body.rstrip()}\n\n{head}{note}" if head else body


def _failures(text, limit):
    """落ちた試験ごとの名前とトレースバックだけにする。通った試験の行は入れず、同じトレースバックは最初の 1 つだけ残し、全体は limit 字以内。
    受けるのは、unittest の生の出力（区切り線あり）と、Gate 1 の出力（区切り線の無い digest）のどちらでもよい。
    落ちた試験の行もトレースバックも見つからない出力（構文の誤り・時間切れなど）は、そのまま limit 字に切る。"""
    found = list(_BLOCK.finditer(text))
    if not found:
        return text[:limit]
    head = [ln for ln in text[:found[0].start()].splitlines() if ln.strip() and not ln.rstrip().endswith(" ... ok")][:HEAD_LINES]
    blocks, seen = [], set()
    for m in found:
        block = m.group(0).split("\n\n")[0].rstrip()   # トレースバックは最初の空行まで（後ろに続く通った試験の行・集計を入れない）
        if block.startswith("Traceback"):   # 例外の行（字下げの無い最初の行）まで。後ろに続く無関係な出力で同じトレースバックを別物にしない
            lines = block.splitlines()
            end = next((i for i, ln in enumerate(lines[1:], 1) if ln and not ln[0].isspace()), len(lines) - 1)
            block = "\n".join(lines[:end + 1])
        if block.startswith("Traceback") and block in seen:
            continue
        seen.add(block)
        blocks.append(block)
    return ("\n".join(head + blocks) + "\n")[:limit]
