"""T2 の固定の前提（P-T2-02〜04）が、ランダムな 50 手の中で成り立つかの粗い見積もり。

近似：盤面は空（固定ブロックは無視）、形は 4 マスの横棒で代表、重力は PR-01 ティックごとに 1 段、ソフトドロップは
押している間 PR-02 ティックごとに 1 段、接地中の移動・回転の成功でロック猶予をリセット（PR-04 回まで）、
PR-03 ティック接地が続けば固定。ハードドロップの有無で分ける。開始の Y は 0〜21 の一様。
"""
import random

GRAV, SOFT, LOCK, RESETS, W = 60, 3, 30, 15, 10


def run(rnd, steps=50, harddrop=False):
    y = rnd.randrange(22)
    x = rnd.randrange(W - 3)
    g = s = lock = resets = 0
    for _ in range(steps):
        L, R, CW, CCW, SD = [rnd.random() < .25 for _ in range(5)]
        HD = rnd.random() < .125
        if harddrop and HD:
            return True
        moved = False
        if L and not R and x > 0:
            x -= 1
            moved = True
        if R and not L and x < W - 4:
            x += 1
            moved = True
        if CW or CCW:
            moved = True   # 回転の成功を移動と同じに扱う
        g += 1
        s = s + 1 if SD else 0
        if y > 0 and (g >= GRAV or (SD and s >= SOFT)):
            y -= 1
            g = s = 0
        if y == 0:
            if moved and resets < RESETS:
                lock, resets = 0, resets + 1
            else:
                lock += 1
            if lock >= LOCK:
                return True
    return False


rnd = random.Random(7)
for hd in (False, True):
    p = sum(run(rnd, harddrop=hd) for _ in range(20000)) / 20000
    print(f"harddrop={hd}: P(固定が 1 系列で起きる)={p:.4f}  30+3 系列で 0 回={(1 - p) ** 33:.4f}")
