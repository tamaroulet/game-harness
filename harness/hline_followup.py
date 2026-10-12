"""H ライン（harness/hline.py）の、前の差分から続ける試行（2 回目から・再開・延長）に渡す失敗の出力の絞り込み。
最初の試行に渡す出力は変えない。"""
import gate_order

HEAD_LINES = 5   # 落ちた試験の前に残す見出しの行数の上限


def narrow(feedback, limit):
    """落ちた試験ごとの名前とトレースバックだけにする。通った試験の行は入れず、同じトレースバックは最初の 1 つだけ残し、全体は limit 字以内。
    落ちた試験の行もトレースバックも見つからない出力（構文の誤り・時間切れなど）は、そのまま limit 字に切る。"""
    text = feedback or ""
    found = list(gate_order._FAILURE.finditer(text))
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
