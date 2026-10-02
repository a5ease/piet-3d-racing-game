# -*- coding: utf-8 -*-
# P4 门槛测试：路面切线偏航角 road_yaw 搬进 Piet（帧段，EXT_DRAW_TRACK 之前）
#   ① 200 帧逐位一致（含 100000 回绕后小 sz ＝负 cos 区）
#   ② 顺序铁律：偏航必须锁定"积分前"的 sz（与相机/渲染同源）
#   ③ 数学正确性：对连续解析真值 atan(0.208333·cos(sz/1200)) 偏差 ≤3 mrad
#   ④ 与旧 Python 量化差分公式的差异，并量化"旧版逐帧抖动" vs "新版平滑"
#   ⑤ 半周期反号 / 极值 / 零点
#   ⑥ INIT 只跑一次 + 帧段命令一致 + 源码门禁
#   ⑦ 单帧执行步数基准
import math
import os
import sys
import time

os.environ['SDL_VIDEODRIVER'] = 'dummy'
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from racing_game import (PietInterpreter, build_game_core, ROAD_YAW_KEY, ROAD_YAW_SCALE)

FAILS = []
D1 = 6_000_000
D2 = 120_000_000_000_000
D3 = 5_040_000_000_000_000_000_000
D23 = D2 * D3
D13 = D1 * D3
C0 = D1 * D2 * D3
MAX_Z = 100000
HALF = 3770            # 1200·π ≈ 3769.9：cos 反号的整数步长


def check(name, cond, detail=''):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f'  ({detail})' if detail else ''), flush=True)
    if not cond:
        FAILS.append(name)


def piet_sin(x):
    """sin 宏的逐位镜像（VM floor 语义；x 可为负——MOD 归约 + f 折叠自动带符号）"""
    x2 = x % 6283
    f = 1 if x2 > 3141 else 0
    x2p = x2 - 3141 * f
    g = 1 if x2p > 1570 else 0
    x3 = (1 - 2 * g) * x2p + 3141 * g
    q = x3 * x3
    P = ((D13 - q) * q - D23) * q + C0
    s = x3 * P // C0
    return s * (1 - 2 * f)


def road_yaw_piet(sz):
    """_emit_road_yaw 的逐位镜像（sz = 该帧积分前的 persist[2001]）"""
    cosF = piet_sin(sz * 1000 // 1200 + 1571)      # cos(sz/1200)·1000
    term1 = 5 * cosF // 24                         # t·1000
    c2 = cosF * cosF // 1000
    c3 = c2 * cosF // 1000
    corr = c3 * 3 // 1000                          # t³/3·1000
    return term1 - corr


def yaw_analytic(sz):
    """和差化积的连续解析真值（mrad）：atan(0.2083333·cos(sz/1200))·1000"""
    return math.atan(0.20833333 * math.cos(sz / 1200.0)) * 1000.0


def yaw_old_python(sz):
    """P4 之前的 Python 公式：floor 量化差分的 atan2（mrad）"""
    cx = lambda z: int(math.sin(z / 1200.0) * 250)
    return math.atan2(cx(sz + 40) - cx(sz - 40), 80.0) * 1000.0


def main():
    print('== P4 车辆偏航角进 Piet 验证 ==', flush=True)
    t0 = time.time()
    img, _, _, _ = build_game_core()
    print(f'  图片: {img.size}  编译耗时 {time.time()-t0:.1f}s', flush=True)
    path = os.path.join(HERE, '_p4_core.png')
    img.save(path)

    vm = PietInterpreter(path, codel_size=1, halt_on_backward=False, max_steps=4_000_000)
    vm.reset()
    cmds1 = vm.run()
    p = vm.runtime['persist']
    check('INIT 标志置位', p.get(69003) == 1)
    check('帧1 产出 swap', any(c[0] == 'swap' for c in cmds1))
    check('帧1 偏航键被写入（sz=0 → 最大角）',
          p.get(ROAD_YAW_KEY) == road_yaw_piet(0),
          f'got={p.get(ROAD_YAW_KEY)} ref={road_yaw_piet(0)} mrad')

    base_speed = p.get(3036, 8)

    # ── ① 逐帧对拍：偏航用【积分前】的 sz（先逼近回绕，落在小 sz／负 cos 区）──
    n_frames = 200
    p[2001] = 99999
    z = 99999
    bad = 0
    for f in range(1, n_frames + 1):
        vm.reset()
        vm.run()
        exp = road_yaw_piet(z)
        if p.get(ROAD_YAW_KEY) != exp:
            bad += 1
            if bad <= 3:
                print(f'    帧{f} yaw got={p.get(ROAD_YAW_KEY)} ref={exp}（sz={z}）', flush=True)
        z = (z + base_speed) % MAX_Z
    check(f'连续 {n_frames} 帧偏航逐位一致', bad == 0, f'{bad} 处不符')
    check('帧末 persist[2001] 与镜像递推一致', p.get(2001) == z, f'got {p.get(2001)} ref {z}')

    # ── ② 顺序铁律：偏航必须锁定"积分前"的 sz ──
    #    取 sz=1885（cos≈0 → |dyaw/dsz| 最大）+ 速度钳到上限 45，把前后帧差异放大到 ~7 mrad
    p[2001] = 1885
    p[2002] = 44
    vm.runtime['keys'][202] = 1            # 按住 ↑：本帧速度 → 45（钳位上限）
    vm.reset(); vm.run()
    check('积分：1885+45 → 1930', p.get(2001) == 1930, f'got {p.get(2001)}')
    check('偏航用积分前 sz（mirror(1885) 而非 mirror(1930)）',
          p.get(ROAD_YAW_KEY) == road_yaw_piet(1885)
          and abs(road_yaw_piet(1885) - road_yaw_piet(1930)) >= 5,
          f'got={p.get(ROAD_YAW_KEY)} ref1885={road_yaw_piet(1885)} ref1930={road_yaw_piet(1930)}')
    vm.runtime['keys'][202] = 0
    p[2002] = base_speed

    # ── ③ 数学正确性：对连续解析真值的偏差 ──
    w_an = 0
    for sz in range(0, MAX_Z, 7):
        w_an = max(w_an, abs(road_yaw_piet(sz) - yaw_analytic(sz)))
    check('对解析真值 atan(0.208333·cos(sz/1200)) 偏差 ≤3 mrad', w_an <= 3.0,
          f'最大 {w_an:.2f} mrad（≡ 角误差 <0.003°，鼻端横向 <0.16 世界单位）')

    # ── ④ 与旧公式的差异界 + 抖动对比（旧版 floor 量化是噪声源）──
    w_old = 0
    jit_old = jit_new = 0
    for sz in range(0, MAX_Z - 40):
        w_old = max(w_old, abs(road_yaw_piet(sz) - yaw_old_python(sz)))
        jit_old = max(jit_old, abs(yaw_old_python(sz + base_speed) - yaw_old_python(sz)))
        jit_new = max(jit_new, abs(road_yaw_piet(sz + base_speed) - road_yaw_piet(sz)))
    check('与旧公式偏差 ≤40 mrad（含旧版自身量化噪声）', w_old <= 40.0,
          f'最大 {w_old:.1f} mrad（≡ {math.degrees(w_old/1000):.2f}°）')
    check('逐帧增量：新版比旧版平滑（无 floor 抖动）', jit_new < jit_old,
          f'旧 max|Δ|/帧 {jit_old:.1f} mrad vs 新 {jit_new:.1f} mrad')

    # ── ⑤ 半周期反号 / 极值 / 零点 ──
    sym_bad = [sz for sz in range(0, MAX_Z - HALF - 1, 311)
               if abs(road_yaw_piet(sz) + road_yaw_piet(sz + HALF)) > 3]
    check(f'半周期反号 yaw(sz) ≈ −yaw(sz+{HALF})', not sym_bad, f'不符 {sym_bad[:3]}')
    y0 = road_yaw_piet(0)
    check('sz=0 取最大正角（≈205 mrad）', 200 <= y0 <= 210, f'{y0} mrad')
    check('负 cos 区取负角', road_yaw_piet(4000) < 0, f'sz=4000 → {road_yaw_piet(4000)} mrad')
    p[2001] = 1885                        # sz/1200 ≈ π/2 → cos≈0 → yaw≈0
    p[2002] = 0
    vm.reset(); vm.run()
    check('cos≈0 处偏航≈0', abs(p.get(ROAD_YAW_KEY)) <= 2,
          f'sz=1885 → {p.get(ROAD_YAW_KEY)} mrad')
    p[2002] = base_speed

    # ── ⑥ INIT 只跑一次（新 VM 继承 _frame_start + 整体继承 persist）──
    vm2 = PietInterpreter(path, codel_size=1, halt_on_backward=False, max_steps=100_000)
    vm2._frame_start = vm._frame_start
    vm2.runtime['persist'].update(p)          # 整体继承（含 sin/LCG 常数，缺则 sin 链退化 0）
    vm2.runtime['persist'][2001] = 12345
    vm2.reset()
    cmds2 = vm2.run()
    check('帧2 限步内见 swap（INIT 未重跑）', any(c[0] == 'swap' for c in cmds2))
    check('帧2 偏航被正确算出（sz=12345）',
          vm2.runtime['persist'].get(ROAD_YAW_KEY) == road_yaw_piet(12345),
          f"got {vm2.runtime['persist'].get(ROAD_YAW_KEY)} ref {road_yaw_piet(12345)}")
    # hud 命令的参数是 persist 派生数据（P3 起 = 速度/计时/圈号），按设计逐帧可不同；
    # 一致性检查只对"结构性"命令（窗口/天空/赛道/参与者）有意义。
    check('帧2/帧1 帧段命令一致（排除 persist 派生的 hud）',
          [c for c in cmds1 if c[0] not in ('swap', 'hud')] ==
          [c for c in cmds2 if c[0] not in ('swap', 'hud')])

    # ── ⑦ 源码门禁：决策确实离开了渲染层 ──
    with open(os.path.join(HERE, 'racing_game.py'), encoding='utf-8') as fh:
        src = fh.read()
    body = src[src.index('def _draw_car3d_gl'):src.index('def _draw_player_gl')]
    check('_draw_car3d_gl 内不再有 atan2', 'atan2' not in body)
    check('_draw_car3d_gl 内不再调 _road_cx', '_road_cx(' not in body)
    check('_draw_car3d_gl 改为读 ROAD_YAW_KEY', 'ROAD_YAW_KEY' in body)
    check('_emit_road_yaw 定义 1 次 + 调用 1 次',
          src.count('_emit_road_yaw') == 2, f"出现 {src.count('_emit_road_yaw')} 次")
    check('_emit_road_yaw 在 draw_track 之前调用',
          src.index('_emit_road_yaw(a, lib)') < src.index('PC(a, EXT_DRAW_TRACK)'))

    # ── ⑧ 性能：单帧执行步数 ──
    vm3 = PietInterpreter(path, codel_size=1, halt_on_backward=False, max_steps=4_000_000)
    vm3.reset(); vm3.run()
    steps = []
    for _ in range(5):
        vm3.reset()
        t1 = time.time()
        vm3.run()
        steps.append((vm3.step_count_last, time.time() - t1))
    avg_step = sum(s for s, _ in steps) / len(steps)
    avg_ms = sum(t for _, t in steps) / len(steps) * 1000
    check('单帧步数 ≤ 8000', avg_step <= 8000,
          f'平均 {avg_step:.0f} 步 / {avg_ms:.1f}ms（P3 时 4822 步）')

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
