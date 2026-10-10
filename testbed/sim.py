"""模型（床・自由な箱・重力・刻み）と、歩を進める入口 run。"""
import mujoco

MODEL_XML = """
<mujoco>
  <option timestep="0.002" gravity="0 0 -9.81"/>
  <worldbody>
    <geom name="floor" type="plane" size="0 0 1" pos="0 0 0" solref="0.004 1"/>
    <body name="box" pos="0 0 1">
      <freejoint name="free"/>
      <geom name="cube" type="box" size="0.05 0.05 0.05" mass="1" solref="0.004 1"/>
    </body>
  </worldbody>
</mujoco>
"""

MIN_HEIGHT = 0.2
MAX_HEIGHT = 2.0


def run(height, steps):
    """中心の初めの高さ height から steps 歩進め、各歩の後の {"qpos", "qvel"} を並べて返す。"""
    if not MIN_HEIGHT <= height <= MAX_HEIGHT:
        raise ValueError(f"height は {MIN_HEIGHT} 以上 {MAX_HEIGHT} 以下: {height}")
    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
        raise ValueError(f"steps は 1 以上の整数: {steps}")
    model = mujoco.MjModel.from_xml_string(MODEL_XML)
    data = mujoco.MjData(model)
    data.qpos[:] = [0.0, 0.0, float(height), 1.0, 0.0, 0.0, 0.0]
    data.qvel[:] = 0.0
    mujoco.mj_forward(model, data)
    states = []
    for _ in range(steps):
        mujoco.mj_step(model, data)
        states.append({"qpos": [float(v) for v in data.qpos],
                       "qvel": [float(v) for v in data.qvel]})
    return states
