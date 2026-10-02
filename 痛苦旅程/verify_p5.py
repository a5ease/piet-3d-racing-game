# -*- coding: utf-8 -*-
"""verify_p5.py —— 载具姿态迁移（C2/P5）：逐帧对拍 + 时机对拍 + 源码门禁

P5 把 roll / steer / 轮自转 从 Python 的 `_car_dyn` 滤波搬进 Piet 帧段：
      6200 CAR_ROLL_KEY   6202 CAR_STEER_KEY   6204 CAR_SPIN_KEY    （毫弧度，S=1000）
      6206 CAR_PREVWX_KEY 6208 CAR_POSE_SEED_KEY （滤波状态，同样住 persist）

为什么需要这个脚本：
  `verify_decoration.py` 只能证明"命令执行阶段不写 persist"，证明不了 Piet 算出来的
  姿态**数值正确**。姿态是纯数值迁移，必须拿旧实现的等价复现逐帧对拍。

四项断言：
  ① 逐帧对拍：以旧 Python `_car_dyn` 滤波（浮点）的等价复现为参照，比较 Piet 定点输出。
  ② 时机对拍：spin 必须严格等于"本帧积分前 sz / 10"（= 渲染期 self.scroll_z）。
     若把发射点误挪到 M3-A 前向积分之后，会整体快一帧 —— 这里显式抓这种回归。
     附反证：证明"积分前/后 sz"确实不同，否则该断言没有区分力。
  ③ 防空真：注入左/右键制造横向位移，dx 正负交替，roll/steer 确实在变。
  ④ 源码门禁：`_draw_car3d_gl` 内不得再有 Python 侧姿态决策残留。

参照来源（标注出处便于复核）—— P5 之前的 racing_game.py `_draw_car3d_gl` 内：
      dx = 0.0 if dyn['prev_wx'] is None else (wx - dyn['prev_wx'])
      dyn['prev_wx'] = wx
      dyn['roll']  = dyn['roll']  * 0.75 + clamp(dx * 0.01, -0.14, 0.14) * 0.25
      dyn['steer'] = dyn['steer'] * 0.7  + clamp(dx * 0.02, -0.45, 0.45) * 0.3
      spin = wz / 10.0
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_VIDEO_WINDOW_POS', '2000,2000')
os.environ.setdefault('PIET_FORCE_2D', '1')

from racing_game import (PietRuntime, CAR_ROLL_KEY, CAR_STEER_KEY, CAR_SPIN_KEY,
                         CAR_PREVWX_KEY, CAR_POSE_SEED_KEY, CAR_POSE_SCALE)

MAX_STEPS = 4_000_000
FRAMES = 16
TOL_MRAD = 5.0        # |Δ| 容差（毫弧度）：整数 floor vs 浮点，偏差有界稳态
FAILS = []


def check(name, cond, detail=''):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f'  ({detail})' if detail else ''),
          flush=True)
    if not cond:
        FAILS.append(name)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def keys_for(f):
    """受控输入：先加速直行 → 右移 → 左移；制造 dx 正负交替。"""
    return {202: 1 if 1 <= f <= 6 else 0,
            201: 1 if 2 <= f <= 5 else 0,
            200: 1 if 7 <= f <= 10 else 0}


def main():
    print('== verify_p5：载具姿态迁移（roll/steer/spin）逐帧对拍 ==', flush=True)
    core = os.path.join(HERE, 'game_core.png')
    if not os.path.exists(core):
        check('game_core.png 存在', False, '缺失 —— 先跑一次构建')
        print(f'\n== 结果: {len(FAILS)} 项失败 ==', flush=True)
        sys.exit(1)

    rt = PietRuntime(core, codel_size=1, track_data=[], tree_positions=[], building_data=[])
    p = rt.vm.runtime['persist']

    # ── 参照实现：旧 Python 浮点滤波的等价复现 ──
    ref_prev_wx = None
    ref_roll = 0.0
    ref_steer = 0.0

    rows = []
    for f in range(FRAMES):
        rt.vm.runtime['ticks'] = 1000 + f * 16
        kv, ks = keys_for(f), rt.vm.runtime['keys']       # keys 是 list，逐键覆盖（含清零）
        for idx in (200, 201, 202, 203):
            ks[idx] = kv.get(idx, 0)
        # ② 时机基准：run() 之前 rt.scroll_z 就是本帧 draw_track 会用的 sz
        #   （上一帧的 handle_input 命令同步来的；M3-A 前向积分在本帧稍后才覆盖它）
        sz_render = float(rt.scroll_z)

        rt.vm.reset()
        cmds = rt.vm.run()
        after = dict(p)

        wx = after.get(69010, 0)
        dx = 0.0 if ref_prev_wx is None else (wx - ref_prev_wx)
        ref_prev_wx = wx
        ref_roll = ref_roll * 0.75 + _clamp(dx * 0.01, -0.14, 0.14) * 0.25
        ref_steer = ref_steer * 0.7 + _clamp(dx * 0.02, -0.45, 0.45) * 0.3
        ref_spin = sz_render / 10.0

        rows.append({
            'f': f, 'wx': wx, 'dx': dx, 'sz': sz_render, 'z1': after.get(2001, 0),
            'roll': after.get(CAR_ROLL_KEY, None), 'rroll': ref_roll,
            'steer': after.get(CAR_STEER_KEY, None), 'rsteer': ref_steer,
            'spin': after.get(CAR_SPIN_KEY, None), 'rspin': ref_spin,
        })
        for c in cmds:
            rt._exec_cmd(c)

    # ── ①-a 三个姿态键每帧都必须由 Piet 写出 ──
    missing = [r['f'] for r in rows if None in (r['roll'], r['steer'], r['spin'])]
    check('每帧都写出 6200/6202/6204（Piet）', not missing, f'缺帧 {missing}')

    # ── ①-b 逐帧对拍 ──
    droll = [abs(r['roll'] / CAR_POSE_SCALE - r['rroll']) * 1000 for r in rows]
    check('roll 逐帧对拍（Piet 定点 vs 旧浮点滤波）', max(droll) <= TOL_MRAD,
          f'max|Δ|={max(droll):.3f} mrad（容差 {TOL_MRAD}）')
    dsteer = [abs(r['steer'] / CAR_POSE_SCALE - r['rsteer']) * 1000 for r in rows]
    check('steer 逐帧对拍（Piet 定点 vs 旧浮点滤波）', max(dsteer) <= TOL_MRAD,
          f'max|Δ|={max(dsteer):.3f} mrad（容差 {TOL_MRAD}）')

    # ── ② spin 时机：必须与渲染期 self.scroll_z 严格同源 ──
    dspin = [abs(r['spin'] / CAR_POSE_SCALE - r['rspin']) for r in rows]
    check('spin 每帧严格等于"积分前 sz/10"（时机正确，不快不慢一帧）',
          max(dspin) == 0.0, f'max|Δ|={max(dspin)} rad')
    # 反证：两候选值（积分前/后 sz）必须真的不同，否则上面那条断言无区分力
    assert_speeds = [r['z1'] - r['sz'] for r in rows]
    check('时机反证：积分前/后 sz 确实不同（断言有区分力）',
          any(s != 0 for s in assert_speeds), f'每帧位移 {assert_speeds}')

    # 播种语义：首帧 dx 必须为 0（同旧 prev_wx=None），且 6208 被 Piet 置 1
    check('首帧 dx=0（复现旧 prev_wx=None 语义）', rows[0]['dx'] == 0.0, '')
    check('6208 播种标志由 Piet 写出', p.get(CAR_POSE_SEED_KEY) == 1, '')
    check('6206 上帧 car_wx 由 Piet 维护', p.get(CAR_PREVWX_KEY) == rows[-1]['wx'],
          f'6206={p.get(CAR_PREVWX_KEY)} wx={rows[-1]["wx"]}')

    # ── ③ 防空真 ──
    rolls = [r['roll'] for r in rows[2:]]
    steers = [r['steer'] for r in rows[2:]]
    dxs = [r['dx'] for r in rows[2:]]
    check('非空真：dx 有正有负', any(d > 0 for d in dxs) and any(d < 0 for d in dxs), f'dx={dxs}')
    check('非空真：roll 非零且随 dx 变化', len(set(rolls)) > 2 and any(v != 0 for v in rolls),
          f'roll={rolls}')
    check('非空真：steer 正负都出现（确实跟随转向）',
          any(v > 0 for v in steers) and any(v < 0 for v in steers), f'steer={steers}')
    check('非空真：姿态未越界（roll ±140 / steer ±450 mrad）',
          all(-140 <= v <= 140 for v in rolls) and all(-450 <= v <= 450 for v in steers), '')

    # ── ④ 源码门禁 ──
    src = io.open(os.path.join(HERE, 'racing_game.py'), encoding='utf-8').read()
    head = 'def _draw_car3d_gl(self, cols):'
    i = src.index(head)
    j = src.index('\n    def ', i + 10)          # 下一个方法定义
    body = src[i:j]
    # 门禁只看**代码**，剥离注释行/行尾注释 —— 否则解释性注释里的历史符号会误报
    code = '\n'.join(ln.split('#', 1)[0] for ln in body.split('\n'))
    check('源码门禁：_draw_car3d_gl 从 persist 读姿态',
          all(k in code for k in ('CAR_ROLL_KEY', 'CAR_STEER_KEY', 'CAR_SPIN_KEY')), '')
    banned = [t for t in ("dyn['roll']", "dyn['steer']", 'self._car_dyn', 'spin = wz',
                          "dx = 0.0 if", "'prev_wx'") if t in code]
    check('源码门禁：无 Python 侧姿态决策残留（仅看代码，注释不计）', not banned,
          f'残留 {banned}')
    check('源码门禁：_car_dyn 实例变量已彻底删除', 'self._car_dyn' not in src, '')

    # ── ⑤ 渲染层证据：姿态数值真的进入车体几何 ──
    #   verify_decoration 只证明"不写 persist"、verify_p5 ① 只证明"数值对"，
    #   都证明不了渲染器**消费**了这三个键。这里用一个记录桩 GL 直接调
    #   `_draw_car3d_gl`，比较不同 persist 姿态下的顶点集合。
    COLS = {'body': (200, 60, 60), 'tire': (30, 30, 30), 'win': (120, 180, 220),
            'wing': (180, 180, 185)}

    class _StubGL:
        ready = True

        def __init__(self):
            self.quads, self.tris = [], []

        def begin_3d(self, **kw):
            pass

        def add_quad3d(self, *v):
            self.quads.append(v)

        def add_tri3d(self, *v):
            self.tris.append(v)

        def flush3d(self):
            pass

        def end_3d(self):
            pass

    def render_car(**pose):
        stub = _StubGL()
        rt.gl = stub
        rt._cam3d = {'eye': (0.0, 100.0, -300.0), 'center': (0.0, 0.0, 0.0),
                     'fov': 250, 'cam_h': 80}
        rt._car_world = (0.0, 0.0, 500.0)
        p[69038] = 0
        p[CAR_ROLL_KEY] = pose.get('roll', 0)
        p[CAR_STEER_KEY] = pose.get('steer', 0)
        p[CAR_SPIN_KEY] = pose.get('spin', 0)
        ok = rt._draw_car3d_gl(COLS)
        return ok, stub

    ok0, s0 = render_car()
    check('渲染桩：_draw_car3d_gl 正常执行', ok0 and len(s0.quads) > 20,
          f'{len(s0.quads)} 四边形 / {len(s0.tris)} 三角形')

    # ⑤-a roll → 贴地阴影四边形（quads[0]）的 y 展布必须 == 72·sin(roll)
    okr, sr = render_car(roll=140)
    if s0.quads and sr.quads:
        y0 = [s0.quads[0][i][1] for i in range(4)]
        yr = [sr.quads[0][i][1] for i in range(4)]
        spread0, spreadr = max(y0) - min(y0), max(yr) - min(yr)
        import math as _m
        exp = 72 * _m.sin(140 / CAR_POSE_SCALE)
        check('roll 进入几何：阴影四边形 y 展布 = 72·sin(roll)',
              abs(spread0) < 1e-9 and abs(spreadr - exp) < 0.05,
              f'roll=0 → {spread0:.6f}；roll=140mrad → {spreadr:.4f}（期望 {exp:.4f}）')

    # ⑤-b steer → 只改两个前轮（轮心由 roll 定，故仅 2×8 个四边形变化，且连续）
    oks, ss = render_car(steer=450)
    if s0.quads and ss.quads and len(s0.quads) == len(ss.quads):
        ch = [i for i, (a, b) in enumerate(zip(s0.quads, ss.quads)) if a != b]
        contiguous = all(ch[i + 1] - ch[i] == 1 for i in range(len(ch) - 1))
        check('steer 进入几何：恰好两个前轮的四边形改变（其余全同）',
              len(ch) == 16 and contiguous,
              f'改变 {len(ch)} 个四边形 @ {ch[:1]}..{ch[-1:]}；连续性={contiguous}')
    else:
        check('steer 进入几何', False, '四边形数不一致')

    # ⑤-c spin → 轮面自转；同样只影响四个轮子
    oksp, ssp = render_car(spin=1000)
    if s0.quads and ssp.quads:
        ch2 = [i for i, (a, b) in enumerate(zip(s0.quads, ssp.quads)) if a != b]
        check('spin 进入几何：仅轮子四边形改变', 0 < len(ch2) <= 40 and ch2[0] > 20,
              f'改变 {len(ch2)} 个四边形')

    # ── 逐帧表 ──
    print('\n   帧  sz_render  car_wx    dx |  roll/Piet    ref      |  steer/Piet   ref',
          flush=True)
    for r in rows:
        print(f'   {r["f"]:>2}  {r["sz"]:>9.0f}  {r["wx"]:>7}  {r["dx"]:>5.0f} | '
              f'{r["roll"]:>6} {r["rroll"] * 1000:>9.3f} | '
              f'{r["steer"]:>6} {r["rsteer"] * 1000:>9.3f}', flush=True)

    print()
    if FAILS:
        print(f'== 结果: {len(FAILS)} 项失败 ==', flush=True)
        for n in FAILS:
            print(f'   - {n}', flush=True)
        sys.exit(1)
    print('== 结果: 全部通过 ==', flush=True)
    sys.exit(0)


if __name__ == '__main__':
    main()
