"""投射の模型。x が前方、y が横（常に 0）、z が高さ。床は z = FLOOR。

半陰的オイラー法で一定の刻み DT ごとに進める。床に着いたら z を FLOOR に留め、
垂直の速度を 0 にし、前方の速度を摩擦で減らす（符号は変えない）。
"""
import math

G = 9.81
DT = 0.005
FLOOR = 0.02
FRICTION = 0.99

ANGLE_RANGE = (5.0, 85.0)
SPEED_RANGE = (0.5, 10.0)
STEPS_RANGE = (1, 20000)


def _number(name, value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} は数でなければならない: {value!r}")
    try:
        x = float(value)
    except OverflowError:
        raise ValueError(f"{name} が範囲外: {value!r}")
    if not (low <= x <= high):
        raise ValueError(f"{name} が範囲外（{low}〜{high}）: {value!r}")
    return x


def validate(angle, speed, steps):
    a = _number("angle", angle, *ANGLE_RANGE)
    s = _number("speed", speed, *SPEED_RANGE)
    if isinstance(steps, bool) or not isinstance(steps, int):
        raise ValueError(f"steps は整数でなければならない: {steps!r}")
    if not (STEPS_RANGE[0] <= steps <= STEPS_RANGE[1]):
        raise ValueError(f"steps が範囲外（{STEPS_RANGE[0]}〜{STEPS_RANGE[1]}）: {steps!r}")
    return a, s, steps


def simulate(angle, speed, steps):
    a, s, steps = validate(angle, speed, steps)
    rad = math.radians(a)
    x, z = 0.0, FLOOR
    vx, vz = s * math.cos(rad), s * math.sin(rad)
    out = []
    for _ in range(steps):
        vz -= G * DT
        x += vx * DT
        z += vz * DT
        if z <= FLOOR:
            z = FLOOR
            vz = 0.0
            vx *= FRICTION
        out.append({"pos": [x, 0.0, z], "vel": [vx, 0.0, vz]})
    return out
