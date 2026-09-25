"""T5 のキックの性質（P-T5-01・03）の前提が、ランダムな系列の中でどれだけ成り立つかの粗い見積もり（v2-smoke-01 の後）。

    python experiments/v2/tools/vacuity_mc.py <系列の長さ>

参照データ（形・キック表）は構造化仕様から propgen と同じ方法で引く。開始状態と入力の分布は propgen の生成物の
Start・RandomInput を写した。遷移は近似（重力とロック猶予を無視、落下は y を 1 減らす、ハードドロップで固定して
(3, 19) に出す）で、乱数も C# とは違う。桁を知るためのもので、生成したテストの結果の代わりにはならない。
"""
import sys, json, random
sys.path.insert(0, "harness")
import project, unit_schema, propgen
proj = project.load("falling-blocks"); cfg = project.config("unit_schema")
m = json.load(open("experiments/v2/tasks.json", encoding="utf-8"))
read = unit_schema.git_reader(proj["repo_dir"], m["base_commit"], 120)
contract = json.load(open("experiments/v2/contract.json", encoding="utf-8"))
props = json.load(open("experiments/v2/properties.json", encoding="utf-8"))
rows = propgen.param_rows(read(cfg["spec_path"]))
ref = propgen.reference(props["reference"], rows, propgen.Contract(contract))
W, H = ref["width"], ref["height"]; TYPES = list(ref["shapes"])
def cells(t, x, y, r): return [(x + cx, y + cy) for cx, cy in ref["shapes"][t][r]]
def fits(occ, t, x, y, r): return all(0 <= a < W and 0 <= b < H and not occ[a][b] for a, b in cells(t, x, y, r))
def kick(occ, t, x, y, r, d):
    to = (r + d) % 4
    for dx, dy in ref["kicks"][t][(r, to)]:
        if fits(occ, t, x + dx, y + dy, to): return (x + dx, y + dy, to)
    return None
def start(rnd):
    while True:
        occ = [[False] * H for _ in range(W)]
        for _ in range(rnd.randint(0, 12)):
            a, b = rnd.randrange(W), rnd.randrange(H // 2); occ[a][b] = True
        mx = (rnd.randint(-2, 0) if rnd.random() < .5 else rnd.randint(W - 4, W - 1)) if rnd.random() < .5 else rnd.randint(-2, W - 1)
        t = rnd.choice(TYPES); y = rnd.randrange(H); r = rnd.randrange(4)
        if fits(occ, t, mx, y, r): return occ, [t, mx, y, r]
def run(rnd, steps=50):
    occ, p = start(rnd); g = {1: 0, -1: 0}
    for _ in range(steps):
        L, R, CW, CCW, SD, HD = [rnd.random() < .25 for _ in range(5)] + [rnd.random() < .125]
        if p is None: continue
        t, x, y, r = p
        for d, flag, others in ((1, CW, (CCW, L, R, SD, HD)), (-1, CCW, (CW, L, R, SD, HD))):
            if flag and not any(others) and not fits(occ, t, x, y, (r + d) % 4) and kick(occ, t, x, y, r, d): g[d] += 1
        # approximate transition: moves, rotation(with kick), soft drop, hard drop(lock+respawn)
        for dx, f in ((-1, L), (1, R)):
            if f and fits(occ, t, x + dx, y, r): x += dx
        for d, f in ((1, CW), (-1, CCW)):
            if f:
                k = (x, y, (r + d) % 4) if fits(occ, t, x, y, (r + d) % 4) else kick(occ, t, x, y, r, d)
                if k: x, y, r = k
        if SD and fits(occ, t, x, y - 1, r): y -= 1
        if HD:
            while fits(occ, t, x, y - 1, r): y -= 1
            for a, b in cells(t, x, y, r): occ[a][b] = True
            t, x, y, r = rnd.choice(TYPES), 3, 19, 0
            if not fits(occ, t, x, y, r): p = None; continue
        p = [t, x, y, r]
    return g
rnd = random.Random(1); N = 2000
res = [run(rnd, int(sys.argv[1])) for _ in range(N)]
for d in (1, -1):
    per = [x[d] for x in res]
    zero1 = sum(1 for v in per if v == 0) / N
    print(f"dir {d}: mean given/sequence {sum(per)/N:.3f}, P(0 in 1 seq)={zero1:.3f}, P(0 in 5 seqs)~{zero1**5:.3f}, P(0 in 20 seqs)~{zero1**20:.4f}")
