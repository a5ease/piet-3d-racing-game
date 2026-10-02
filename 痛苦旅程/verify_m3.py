# -*- coding: utf-8 -*-
# M3 门槛测试：规则搬进 Piet（帧段）
#   ① 前向积分 + 100000 回绕：连续多帧 persist[2001] 与逐位镜像一致
#   ② car_wx / 碰撞判定：persist[2100] 与 Piet 整数镜像逐帧一致（含正碰撞用例）
#   ③ 窗口覆盖等价性：Python 全量 334 树参考集 ⊆ Piet 6 格窗口候选集
#   ④ INIT 只跑一次 + 帧段命令一致（沿用 M2 手法）
#   ⑤ 帧执行步数基准（性能门槛）
import math
import os
import sys
import time

os.environ['SDL_VIDEODRIVER'] = 'dummy'
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from racing_game import PietInterpreter, build_game_core

FAILS = []
D1 = 6_000_000
D2 = 120_000_000_000_000
D3 = 5_040_000_000_000_000_000_000
D23 = D2 * D3
D13 = D1 * D3
C0 = D1 * D2 * D3
MAX_Z = 100000


def check(name, cond, detail=''):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f'  ({detail})' if detail else ''), flush=True)
    if not cond:
        FAILS.append(name)


def piet_sin(x):
    """sin 宏的逐位镜像（x 毫弧度≥0 → sin(x/1000)×1000，VM floor 语义）"""
    x2 = x % 6283
    f = 1 if x2 > 3141 else 0
    x2p = x2 - 3141 * f
    g = 1 if x2p > 1570 else 0
    x3 = (1 - 2 * g) * x2p + 3141 * g
    q = x3 * x3
    P = ((D13 - q) * q - D23) * q + C0
    s = x3 * P // C0
    return s * (1 - 2 * f)


def road_cx_piet(sz):
    """Piet 帧段 car_wx 用的 road_cx 镜像"""
    return piet_sin(sz * 1000 // 1200) * 250 // 1000


def road_cx_float(sz):
    return int(math.sin(sz / 1200.0) * 250)


def car_wx_piet(px, sz, fov):
    return road_cx_piet(sz) + (px - 400) * 220 // fov


def a0_of(sz):
    """碰撞窗口起点：clamp((sz-100)//600, 0, 161)（VM DIVIDE = floor）"""
    return min(161, max(0, (sz - 100) // 600))


def pid_of(p, sz, px):
    """Piet 碰撞循环镜像：[pid] → collision = NOT(pid)"""
    cw = car_wx_piet(px, sz, p.get(3038, 250))
    radius = p.get(3035, 30)
    fov = p.get(3038, 250)
    pid = 1
    a0 = a0_of(sz)
    for j in range(6):
        a = a0 + j
        twz = (600 * a + 100) - sz
        cx = p.get(55000 + 3 * a, 0)
        for wx in (cx + 180, cx - 180):
            d = wx - cw
            hit = 1 if (radius * twz) ** 2 > (d * fov) ** 2 else 0
            rng = (1 if twz >= 0 else 0) * (1 if twz <= 3000 else 0)
            pid *= 1 - rng * hit
    return pid


def collision_piet(p, sz, px):
    return 1 - pid_of(p, sz, px)


def collision_ref(p, sz, px, tree_z_span=3000):
    """原 Python 参考（float，全量树表）——与 Piet 版对拍用"""
    cw_f = road_cx_float(sz) + (px - 400) * 220.0 / max(p.get(3038, 250), 1)
    radius = p.get(3035, 30)
    fov = p.get(3038, 250)
    for a in range(167):
        wz = 600 * a + 100
        twz = wz - sz
        if twz < 0 or twz > tree_z_span:
            continue
        cx = p.get(55000 + 3 * a, 0)
        for wx in (cx - 180, cx + 180):
            if abs(wx - cw_f) < radius * twz / max(fov, 1):
                return 1
    return 0


def ref_candidates(sz, tree_z_span=3000):
    """参考集会命中的树格 a（twz 过滤后）——用于窗口覆盖等价性断言"""
    out = []
    for a in range(167):
        twz = (600 * a + 100) - sz
        if 0 <= twz <= tree_z_span:
            out.append(a)
    return out


def main():
    print('== M3 规则进 Piet 验证 ==', flush=True)
    t0 = time.time()
    img, _, _, _ = build_game_core()
    print(f'  图片: {img.size}  编译耗时 {time.time()-t0:.1f}s', flush=True)
    path = os.path.join(HERE, '_m3_core.png')
    img.save(path)

    vm = PietInterpreter(path, codel_size=1, halt_on_backward=False, max_steps=4_000_000)
    vm.reset()
    cmds1 = vm.run()
    p = vm.runtime['persist']
    check('INIT 标志置位', p.get(69003) == 1)
    check('帧1 产出 swap', any(c[0] == 'swap' for c in cmds1))

    base_speed = p.get(3036, 8)
    # ── ① 帧1：z = 0 + speed ──
    check('帧1 scroll 积分', p.get(2001) == base_speed, f"got {p.get(2001)} 期望 {base_speed}")

    # ── ② 逐帧对拍：persist[2001] 与 2100 ──
    n_frames = 200
    z_bad = col_bad = 0
    z = p.get(2001)
    for f in range(2, n_frames + 1):
        z = (z + base_speed) % MAX_Z          # 镜像递推（单帧回绕）
        vm.reset()
        vm.run()
        if p.get(2001) != z:
            z_bad += 1
            if z_bad <= 3:
                print(f'    帧{f} z got={p.get(2001)} ref={z}', flush=True)
        px = p.get(2000, 400)
        if p.get(2100) != collision_piet(p, p.get(2001), px):
            col_bad += 1
            if col_bad <= 3:
                print(f'    帧{f} col got={p.get(2100)} ref={collision_piet(p, p.get(2001), px)}',
                      flush=True)
    check(f'连续 {n_frames} 帧 scroll 一致', z_bad == 0, f'{z_bad} 帧不符')
    check(f'连续 {n_frames} 帧碰撞一致', col_bad == 0, f'{col_bad} 帧不符')

    # ── ③ 回绕用例 ──
    p[2001] = MAX_Z - 5
    vm.reset(); vm.run()
    exp = (MAX_Z - 5 + base_speed) % MAX_Z
    check('回绕 99995+speed', p.get(2001) == exp, f"got {p.get(2001)} 期望 {exp}")
    check('回绕后碰撞是否一致', p.get(2100) == collision_piet(p, p.get(2001), p.get(2000, 400)))

    # ── ④ 正/负碰撞用例（先用镜像扫出样本，再验证 Piet 一致）──
    def _find_case(want):
        for sz2 in range(0, 30000, 200):
            for px2 in range(50, 751):
                if collision_piet(p, sz2, px2) == want:
                    return sz2, px2
        return None

    for want, label in ((0, '负碰撞（无命中）'), (1, '正碰撞（命中）')):
        case = _find_case(want)
        if case is None:
            check(f'找到{label}用例', False, '镜像扫描未发现样本')
            continue
        sz2, px2 = case
        p[2001] = (sz2 - base_speed) % MAX_Z    # 帧内积分后正好落在 sz2
        p[2000] = px2
        vm.reset(); vm.run()
        check(f'{label} sz={sz2} px={px2}',
              p.get(2001) == sz2 and p.get(2100) == want,
              f"z={p.get(2001)}（期望 {sz2}）col={p.get(2100)}（期望 {want}）")

    # ── ⑤ 窗口覆盖等价性：参考候选集 ⊆ 窗口集 ──
    bad_cover = []
    for sz in range(0, MAX_Z, 37):
        cand = set(ref_candidates(sz))
        a0 = a0_of(sz)
        win = set(range(a0, a0 + 6))
        if not cand <= win:
            bad_cover.append((sz, sorted(cand - win)))
    check('窗口覆盖（候选集 ⊆ a0..a0+5）', not bad_cover,
          f'{len(bad_cover)} 处缺格 例:{bad_cover[:3]}')

    # ── ⑥ 与 float 参考对拍（容差：car_wx 偏差 ≤3；碰撞允许阈值边缘差异）──
    worst_dw = 0
    for sz in range(0, MAX_Z, 311):
        for px in (50, 200, 400, 621, 750):
            dw = abs(car_wx_piet(px, sz, p.get(3038, 250)) -
                     (road_cx_float(sz) + (px - 400) * 220.0 / max(p.get(3038, 250), 1)))
            worst_dw = max(worst_dw, dw)
    check('car_wx 偏差 ≤3（对 float 版）', worst_dw <= 3, f'最大 {worst_dw}')

    # ── ⑦ INIT 只跑一次（帧2 限步内见 swap）──
    vm2 = PietInterpreter(path, codel_size=1, halt_on_backward=False, max_steps=100_000)
    vm2._frame_start = vm._frame_start
    # 必须【整体继承】persist：帧段命令携带 persist 派生数据（P3 起 HUD 的 arg = speed[2002]），
    # 缺键会让帧2 命令与帧1 不同。同款教训见 verify_m4 ⑤。
    vm2.runtime['persist'].update(p)
    vm2.reset()
    cmds2 = vm2.run()
    check('帧2 限步内见 swap（INIT 未重跑）', any(c[0] == 'swap' for c in cmds2))
    # hud 命令的参数是 persist 派生数据（P3 起 = 速度/计时/圈号），按设计逐帧可不同；
    # 一致性检查只对"结构性"命令（窗口/天空/赛道/参与者）有意义。
    check('帧2/帧1 帧段命令一致（排除 persist 派生的 hud）',
          [c for c in cmds1 if c[0] not in ('swap', 'hud')] ==
          [c for c in cmds2 if c[0] not in ('swap', 'hud')])

    # ── ⑧ 性能：单帧执行步数 ──
    vm3 = PietInterpreter(path, codel_size=1, halt_on_backward=False, max_steps=4_000_000)
    vm3.reset(); vm3.run()                       # INIT 帧
    steps = []
    for _ in range(5):
        vm3.reset()
        t1 = time.time()
        vm3.run()
        steps.append((vm3.step_count_last, time.time() - t1))
    avg_step = sum(s for s, _ in steps) / len(steps)
    avg_ms = sum(t for _, t in steps) / len(steps) * 1000
    check('单帧步数 ≤ 8000', avg_step <= 8000, f'平均 {avg_step:.0f} 步 / {avg_ms:.1f}ms')

    try:
        os.remove(path)
    except OSError:
        pass

    print()
    if FAILS:
        print(f'== 结果: {len(FAILS)} 项失败 ==')
        sys.exit(1)
    print('== 结果: 全部通过 ==')


if __name__ == '__main__':
    main()
