"""課題 B01（docs/bench/B01/spec.md）の玉転がし。物理は MuJoCo で計算する。"""
import math
import numbers

import mujoco

G = 9.81
DT = 0.002
LIMIT = 5000
TILT_MAX = 0.2
START = (-0.35, -0.35, 0.02)
GOAL_X, GOAL_Y, GOAL_R = 0.35, 0.35, 0.05
FALL_Z = -0.05

_XML = """
<mujoco>
  <option timestep="0.002" gravity="0 0 -9.81"/>
  <worldbody>
    <geom name="board" type="box" size="0.5 0.5 0.01" pos="0 0 -0.01"/>
    <body name="ball" pos="-0.35 -0.35 0.02">
      <freejoint/>
      <geom name="ball" type="sphere" size="0.02" mass="0.05"/>
    </body>
  </worldbody>
</mujoco>
"""


def _tilt(name, value):
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(f"{name} は数でなければならない: {value!r}")
    value = float(value)
    if not math.isfinite(value) or abs(value) > TILT_MAX:
        raise ValueError(f"{name} は -{TILT_MAX} 以上 {TILT_MAX} 以下の有限の数でなければならない: {value!r}")
    return value


class Game:
    def __init__(self):
        self._model = mujoco.MjModel.from_xml_string(_XML)
        self._data = mujoco.MjData(self._model)
        mujoco.mj_forward(self._model, self._data)
        self._steps = 0
        self._final = None

    def _snapshot(self, outcome):
        return {
            "pos": [float(v) for v in self._data.qpos[0:3]],
            "vel": [float(v) for v in self._data.qvel[0:3]],
            "outcome": outcome,
        }

    def step(self, tx, ty):
        tx = _tilt("tx", tx)
        ty = _tilt("ty", ty)
        if self._final is not None:
            return self._copy(self._final)
        self._model.opt.gravity[:] = (
            G * math.sin(tx), G * math.sin(ty), -G * math.cos(tx) * math.cos(ty))
        mujoco.mj_step(self._model, self._data)
        self._steps += 1
        state = self._snapshot(None)
        x, y, z = state["pos"]
        if z < FALL_Z:
            state["outcome"] = "fell"
        elif z >= 0 and (x - GOAL_X) ** 2 + (y - GOAL_Y) ** 2 <= GOAL_R ** 2:
            state["outcome"] = "goal"
        elif self._steps >= LIMIT:
            state["outcome"] = "timeout"
        if state["outcome"] is not None:
            self._final = state
            return self._copy(state)
        return state

    @staticmethod
    def _copy(state):
        return {"pos": list(state["pos"]), "vel": list(state["vel"]), "outcome": state["outcome"]}


__all__ = ["Game"]
