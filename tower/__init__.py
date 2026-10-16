"""課題 B05（docs/bench/B05/spec.md）の積み木の塔。物理は MuJoCo で計算する。"""
import math
import numbers

import mujoco

H = 0.05
STEPS_STAND = 2000
STAND_RATIO = 0.95
MAX_STEPS = 20000
MAX_K = 6
MAX_ABS = 0.5
MAX_GAP = 0.1
HARMONIC_SPAN = 0.8 * H


def _number(name, value):
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(f"{name} は数でなければならない: {value!r}")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} は有限の数でなければならない: {value!r}")
    return value


def _integer(name, value, lo, hi):
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise ValueError(f"{name} は整数でなければならない: {value!r}")
    value = int(value)
    if not lo <= value <= hi:
        raise ValueError(f"{name} は {lo} 以上 {hi} 以下でなければならない: {value!r}")
    return value


def _offsets(offsets):
    if not isinstance(offsets, (list, tuple)):
        raise ValueError(f"offsets は list か tuple でなければならない: {offsets!r}")
    if not 1 <= len(offsets) <= MAX_K:
        raise ValueError(f"offsets の長さは 1 以上 {MAX_K} 以下でなければならない: {len(offsets)}")
    xs = [_number("offsets の要素", v) for v in offsets]
    if any(abs(x) > MAX_ABS for x in xs):
        raise ValueError(f"offsets の要素の絶対値は {MAX_ABS} 以下でなければならない: {xs!r}")
    if any(abs(b - a) > MAX_GAP for a, b in zip(xs, xs[1:])):
        raise ValueError(f"隣り合う offsets の差の絶対値は {MAX_GAP} 以下でなければならない: {xs!r}")
    return xs


def _xml(xs):
    bodies = "".join(
        f'    <body name="box{j}" pos="{x!r} 0 {0.025 + H * j!r}">\n'
        f'      <freejoint/>\n'
        f'      <geom type="box" size="0.05 0.05 0.025" mass="0.1" solref="0.004 1"/>\n'
        f'    </body>\n'
        for j, x in enumerate(xs))
    return (
        '<mujoco>\n'
        '  <option timestep="0.002" gravity="0 0 -9.81" integrator="Euler"/>\n'
        '  <worldbody>\n'
        '    <geom name="floor" type="plane" size="0 0 1" solref="0.004 1"/>\n'
        f'{bodies}'
        '  </worldbody>\n'
        '</mujoco>\n')


def simulate(offsets, steps):
    xs = _offsets(offsets)
    steps = _integer("steps", steps, 1, MAX_STEPS)
    model = mujoco.MjModel.from_xml_string(_xml(xs))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    states = []
    for _ in range(steps):
        mujoco.mj_step(model, data)
        states.append([
            {"pos": [float(v) for v in data.qpos[7 * j:7 * j + 3]],
             "vel": [float(v) for v in data.qvel[6 * j:6 * j + 3]]}
            for j in range(len(xs))])
    return states


def stands(offsets):
    xs = _offsets(offsets)
    top = simulate(xs, STEPS_STAND)[-1][-1]["pos"][2]
    return bool(top >= STAND_RATIO * (0.025 + H * (len(xs) - 1)))


def harmonic(k):
    k = _integer("k", k, 1, MAX_K)
    xs = [0.0]
    for j in range(k - 1):
        xs.append(xs[-1] + HARMONIC_SPAN / (k - 1 - j))
    return xs


__all__ = ["simulate", "stands", "harmonic"]
