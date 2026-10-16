"""接地した開始状態から、最初の quiet 手の各キーの確率 rate を変えたとき、50 手の中で固定が起きる確率の粗い見積もり（v2.1f）。

近似は lock_mc.py と同じ（盤面は空、横棒、移動・回転でロック猶予がリセット、リセットは 15 回まで）。
"""
import random

GRAV, SOFT, LOCK, RESETS, W = 60, 3, 30, 15, 10


def run(rnd, rate, quiet=31, steps=50):
    y, x = 0, rnd.randrange(W - 3)
    g = s = lock = resets = 0
    for t in range(steps):
        pr = rate if t < quiet else None
        keys = [rnd.random() < (pr if pr is not None else .25) for _ in range(5)]
        L, R, CW, CCW, SD = keys
        moved = False
        if L and not R and x > 0:
            x -= 1
            moved = True
        if R and not L and x < W - 4:
            x += 1
            moved = True
        if CW or CCW:
            moved = True
        if moved and resets < RESETS:
            lock, resets = 0, resets + 1
        else:
            lock += 1
        if lock >= LOCK:
            return True
    return False


rnd = random.Random(3)
for rate in (0.0, 0.01, 0.02, 0.05):
    p = sum(run(rnd, rate) for _ in range(20000)) / 20000
    print(f"rate={rate}: P(lock per sequence)={p:.3f}  P(all 3 miss)={(1 - p) ** 3:.4f}  P(all 5 miss)={(1 - p) ** 5:.5f}")
