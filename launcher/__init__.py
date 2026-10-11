"""課題 B06（docs/bench/B06/spec.md）の投射と狙い。物理は MuJoCo で計算する。"""
import math

import mujoco

G = 9.81
DT = 0.002
RADIUS = 0.02
MASS = 0.1
SOLREF = "0.004 1"
MAX_LANDING_STEPS = 6000

_XML = f"""
<mujoco>
  <option timestep="{DT}" gravity="0 0 -{G}"/>
  <worldbody>
    <geom name="floor" type="plane" size="0 0 0.1" solref="{SOLREF}"/>
    <body name="ball" pos="0 0 {RADIUS}">
      <freejoint/>
      <geom name="ball" type="sphere" size="{RADIUS}" mass="{MASS}" solref="{SOLREF}"/>
    </body>
  </worldbody>
</mujoco>
"""

_model = None


def _get_model():
    global _model
    if _model is None:
        _model = mujoco.MjModel.from_xml_string(_XML)
    return _model


def _number(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} は数でなければならない: {value!r}")
    if not (low <= value <= high):
        raise ValueError(f"{name} は {low} 以上 {high} 以下でなければならない: {value!r}")
    return float(value)


def _check_launch(angle, speed):
    return _number(angle, 5, 85, "angle"), _number(speed, 0.5, 10, "speed")


def _states(angle, speed):
    """打ち出しから 1 歩ずつ進めて (pos, vel) を返す生成器。"""
    model = _get_model()
    data = mujoco.MjData(model)
    mujoco.mj_resetData(model, data)
    theta = math.radians(angle)
    data.qpos[:3] = (0.0, 0.0, RADIUS)
    data.qvel[:3] = (speed * math.cos(theta), 0.0, speed * math.sin(theta))
    data.qvel[3:6] = 0.0
    mujoco.mj_forward(model, data)
    while True:
        mujoco.mj_step(model, data)
        yield ([float(v) for v in data.qpos[:3]], [float(v) for v in data.qvel[:3]])


def simulate(angle, speed, steps):
    angle, speed = _check_launch(angle, speed)
    if isinstance(steps, bool) or not isinstance(steps, int) or not (1 <= steps <= 20000):
        raise ValueError(f"steps は 1 以上 20000 以下の整数でなければならない: {steps!r}")
    out = []
    for pos, vel in _states(angle, speed):
        out.append({"pos": pos, "vel": vel})
        if len(out) == steps:
            return out


def _landing(angle, speed):
    prev_x, prev_z = 0.0, RADIUS
    for i, (pos, _) in enumerate(_states(angle, speed), start=1):
        x, z = pos[0], pos[2]
        if prev_z > RADIUS >= z:
            return float(prev_x + (x - prev_x) * (prev_z - RADIUS) / (prev_z - z))
        if i >= MAX_LANDING_STEPS:
            raise RuntimeError("着地しなかった")
        prev_x, prev_z = x, z


def landing(angle, speed):
    angle, speed = _check_launch(angle, speed)
    return _landing(angle, speed)


def aim(distance, speed):
    if (isinstance(distance, bool) or not isinstance(distance, (int, float))
            or not math.isfinite(distance) or not distance > 0):
        raise ValueError(f"distance は 0 より大きい有限の数でなければならない: {distance!r}")
    speed = _number(speed, 0.5, 10, "speed")
    distance = float(distance)
    low, high = 5.0, 45.0
    if _landing(low, speed) > distance or _landing(high, speed) < distance:
        return None
    for _ in range(60):
        mid = (low + high) / 2
        if _landing(mid, speed) < distance:
            low = mid
        else:
            high = mid
        if high - low < 1e-9:
            break
    return float((low + high) / 2)
