# -*- coding: utf-8 -*-
# P3-1 门槛测试：计时 / 圈数 / 过线检测搬进 Piet 帧段
#   ① 计时：t0 惰性初始化（INIT 期时钟恒 0，不能在那里写 t0）+ elapsed = tick − t0'
#   ② 圈号：INIT 置 1，persist[2001] 回绕 +1；不绕不加
#   ③ 过线标志 69110 逐帧精确等于 ((z+speed) − 99999 > 0)
#   ④ 顺序铁律：用时取本帧 tick（整帧恒定）；圈号取"本帧开始"值（与 EXT_DRAW_TRACK
#      渲染用的 sz 同源），不是本帧刚推进完的圈号——否则渲染出的帧里世界与圈号不同步
#   ⑤ 120 帧逐帧对拍（含一次 100000 回绕）
#   ⑥ 源码门禁：时间分解在 Piet、模板在图片、Python 渲染层不算时间/圈号
#   ⑦ INIT 只跑一次 + 帧2/帧1 非 HUD 命令一致
#   ⑧ 单帧执行步数基准
import os
import sys
import time

os.environ['SDL_VIDEODRIVER'] = 'dummy'
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from racing_game import (PietInterpreter, build_game_core, HUD_KEY0, HUD_KEY1,
                         decode_hud_template)

FAILS = []
MAX_Z = 100000


def check(name, cond, detail=''):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f'  ({detail})' if detail else ''), flush=True)
    if not cond:
        FAILS.append(name)


def timing_piet(t0, tick):
    """emit_timing 的逐位镜像：t0' = t0 + (tick − t0)·(t0 == 0)"""
    is0 = 1 if t0 == 0 else 0
    t0n = t0 + is0 * (tick - t0)
    return t0n, tick - t0n


def wrap_flag(z, s):
    """M3-A 的 flag：flag = (z + s − 99999) > 0（GREATER 是严格大于）"""
    return 1 if (z + s) - 99999 > 0 else 0


def hud_args(cmds, mid):
    for c in cmds:
        if c[0] == 'hud' and c[7] == mid:
            return c[8]
    return None


def main():
    print('== P3-1 计时/圈数/过线检测进 Piet 验证 ==', flush=True)
    t0 = time.time()
    img, _, _, _ = build_game_core()
    print(f'  图片: {img.size}  编译耗时 {time.time()-t0:.1f}s', flush=True)
    path = os.path.join(HERE, '_p3b_core.png')
    img.save(path)

    vm = PietInterpreter(path, codel_size=1, halt_on_backward=False, max_steps=4_000_000)

    # ── ① 帧1：时钟还没起（ticks=0）→ t0 必须仍为 0（"未起跑"），elapsed 0 ──
    vm.runtime['ticks'] = 0
    vm.reset()
    cmds1 = vm.run()
    p = vm.runtime['persist']
    check('INIT 标志置位', p.get(69003) == 1)
    check('帧1 t0 未起跑（ticks=0 → 69100 仍为 0）', p.get(69100) == 0,
          f'got {p.get(69100)}')
    check('帧1 elapsed = 0', p.get(69102) == 0, f'got {p.get(69102)}')
    check('帧1 圈号 = 1（INIT 置初值）', p.get(69104) == 1, f'got {p.get(69104)}')

    base_speed = p.get(3036, 8)
    n_frames = 120

    # ── ② 120 帧逐帧对拍：ticks 带抖动递增，起点逼近 100000 保证中途回绕一次 ──
    #    帧内 Piet 顺序 = emit_timing → HUD → M3-A（积分/圈号）：
    #      用时 → 本帧的（emit_timing 先于 HUD；tick 整帧恒定，等价于"帧开始"）
    #      圈号 → 本帧开始时的（在 HUD 之后的 M3-A 里才推进；与渲染用的 sz 同源）
    z = 99900
    p[2001] = z
    ticks = 0
    t0m, lap_prev = 0, 1
    tm_bad = lap_bad = flag_bad = z_bad = hud_bad = 0
    lag_seen = 0
    laps_total = 1
    for f in range(n_frames):
        ticks += 16 + (f % 3)                      # 模拟 60fps 抖动
        if f == 0:
            ticks = 320                            # 引导帧之后时钟已起
        vm.runtime['ticks'] = ticks
        vm.reset()
        cmds = vm.run()

        # 本帧镜像推进
        t0m, el = timing_piet(t0m, ticks)
        fl = wrap_flag(z, base_speed)
        z = (z + base_speed) - 100000 * fl
        laps_total = lap_prev + fl

        # HUD 计时板参数：用时取本帧、圈号取帧开始值
        a = hud_args(cmds, 2)
        exp_a = (el // 60000, el // 1000 % 60, el // 10 % 100, lap_prev)
        if a != exp_a:
            hud_bad += 1
            if hud_bad <= 3:
                print(f'    帧{f} hud2 got={a} ref={exp_a}', flush=True)

        if p.get(69102) != el:
            tm_bad += 1
            if tm_bad <= 3:
                print(f'    帧{f} 计时 got={p.get(69102)} ref={el}', flush=True)
        if p.get(69110) != fl:
            flag_bad += 1
        if p.get(69104) != laps_total:
            lap_bad += 1
            if lap_bad <= 3:
                print(f'    帧{f} 圈号 got={p.get(69104)} ref={laps_total}', flush=True)
        if p.get(2001) != z:
            z_bad += 1
        if fl and a is not None and a[3] != p.get(69104):
            lag_seen += 1                              # HUD 圈号确实滞后本帧推进
        lap_prev = laps_total

    check(f'连续 {n_frames} 帧计时逐位一致', tm_bad == 0, f'{tm_bad} 处不符')
    check(f'连续 {n_frames} 帧圈号逐位一致', lap_bad == 0, f'{lap_bad} 处不符')
    check(f'连续 {n_frames} 帧过线标志逐位一致', flag_bad == 0, f'{flag_bad} 处不符')
    check(f'连续 {n_frames} 帧 scroll 递推一致', z_bad == 0, f'{z_bad} 处不符')
    check('区间内确实发生过回绕（否则圈号测试无判别力）', laps_total >= 2,
          f'帧末圈号 {laps_total}')
    check('帧末圈号 = 1 + 回绕次数', p.get(69104) == laps_total, f'got {p.get(69104)}')
    check(f'连续 {n_frames} 帧 HUD 计时板参数逐位一致', hud_bad == 0, f'{hud_bad} 处不符')
    check('HUD 圈号确实取"本帧开始"值（滞后 1，非恒真）', lag_seen >= 1,
          f'{lag_seen} 帧可观测到滞后')
    check('计时板模板 = 时间: {}:{:02d}.{:02d}  第 {} 圈',
          decode_hud_template(p, HUD_KEY1) == '时间: {}:{:02d}.{:02d}  第 {} 圈',
          f'got {decode_hud_template(p, HUD_KEY1)!r}')

    # ── ③ 顺序铁律：回绕帧里 HUD 圈号仍是"本帧开始"的圈号 ──
    p[2001] = 99995
    p[2002] = 8
    lap_before = p.get(69104)
    vm.runtime['ticks'] += 20
    vm.reset()
    cmds = vm.run()
    a = hud_args(cmds, 2)
    check('回绕帧：scroll → 3', p.get(2001) == 3, f'got {p.get(2001)}')
    check('回绕帧：过线标志 = 1', p.get(69110) == 1, f'got {p.get(69110)}')
    check('回绕帧：圈号 +1', p.get(69104) == lap_before + 1,
          f'got {p.get(69104)} ref {lap_before + 1}')
    check('顺序铁律：HUD 圈号 = 帧开始值（渲染与本帧世界同源）',
          a is not None and a[3] == lap_before and p.get(69104) == lap_before + 1,
          f'hud={a} 帧末圈号={p.get(69104)}')

    # ── ④ 不绕不加 + 过线标志归零 ──
    lap_now = p.get(69104)
    vm.runtime['ticks'] += 20
    vm.reset()
    cmds = vm.run()
    check('普通帧：过线标志归零', p.get(69110) == 0, f'got {p.get(69110)}')
    check('普通帧：圈号不变', p.get(69104) == lap_now, f'got {p.get(69104)}')
    check('普通帧：scroll 3+8 → 11', p.get(2001) == 11, f'got {p.get(2001)}')

    # ── ⑤ t0 惰性初始化语义 ──
    check('t0 一旦置位便不再被覆盖（== 首个非零 tick 320）',
          p.get(69100) == 320, f'got {p.get(69100)}')
    check('elapsed > 0 且随时间单调', p.get(69102) > 0, f'got {p.get(69102)}')

    # ── ⑥ INIT 只跑一次（帧2 夹具必须整体继承 persist）──
    vm2 = PietInterpreter(path, codel_size=1, halt_on_backward=False, max_steps=100_000)
    vm2._frame_start = vm._frame_start
    vm2.runtime['persist'].update(p)
    vm2.runtime['persist'][2001] = 12345
    vm2.runtime['ticks'] = p.get(69100) + 5000
    vm2.reset()
    cmds2 = vm2.run()
    check('帧2 限步内见 swap（INIT 未重跑）', any(c[0] == 'swap' for c in cmds2))
    check('帧2 计时正确（t0 继承自帧1）',
          vm2.runtime['persist'].get(69102)
          == timing_piet(p.get(69100), p.get(69100) + 5000)[1],
          f"got {vm2.runtime['persist'].get(69102)}")
    check('帧2/帧1 非 HUD 命令一致（HUD 参数本就逐帧不同）',
          [c for c in cmds1 if c[0] not in ('swap', 'hud')]
          == [c for c in cmds2 if c[0] not in ('swap', 'hud')])

    # ── ⑦ 源码门禁：决策在 Piet，呈现才在 Python ──
    with open(os.path.join(HERE, 'racing_game.py'), encoding='utf-8') as fh:
        src = fh.read()
    check('emit_timing 定义 1 次 + 调用 1 次',
          src.count('def emit_timing') == 1 and src.count('emit_timing(a, lib)') == 2,
          f"def={src.count('def emit_timing')} call={src.count('emit_timing(a, lib)')}")
    check('emit_timing 在 EXT_HUD 之前调用（同一帧里数据先于排版）',
          src.index('emit_timing(a, lib)') < src.index('PC(a, EXT_HUD)'))
    check('计时板模板字面量只出现在生成器', src.count("'时间: '") == 1,
          f"出现 {src.count(chr(39) + '时间: ' + chr(39))} 次")
    check('HUD_KEY1 定义/写图/解码 各 1 处', src.count('HUD_KEY1') == 3,
          f"出现 {src.count('HUD_KEY1')} 次")
    check('时间分解（÷60000）在 Piet 里发射，不在 Python 渲染层',
          'PC(a, 60000)' in src)
    check('圈号由 Piet 累加（peek 69104 + ADD）',
          'lib.peek(69104); lib.peek(69110)' in src)
    check('圈号初值由 Piet INIT 写入', 'lib.put_val(1, 69104)' in src)
    check('过线标志由 Piet 写（put 69110）', 'lib.put(69110)' in src)
    body = src[src.index('def _draw_track_gl'):src.index('def _draw_car3d_gl')]
    check('渲染层不算 elapsed（无 tick 差值代码）', 'ticks' not in body)
    check('渲染层不含圈号计数', 'lap' not in body and '69104' not in body)

    # ── ⑧ 性能：单帧执行步数 ──
    vm3 = PietInterpreter(path, codel_size=1, halt_on_backward=False, max_steps=4_000_000)
    vm3.runtime['ticks'] = 1000
    vm3.reset()
    vm3.run()
    steps = []
    for _ in range(5):
        vm3.runtime['ticks'] += 16
        vm3.reset()
        t1 = time.time()
        vm3.run()
        steps.append((vm3.step_count_last, time.time() - t1))
    avg_step = sum(s for s, _ in steps) / len(steps)
    avg_ms = sum(t for _, t in steps) / len(steps) * 1000
    check('单帧步数 ≤ 8000', avg_step <= 8000,
          f'平均 {avg_step:.0f} 步 / {avg_ms:.1f}ms（P4 时 5128 步）')

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
