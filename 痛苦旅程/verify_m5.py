# -*- coding: utf-8 -*-
# M5 门槛测试：生物群系索引搬进 Piet（INIT 写表 persist[60000+i]，运行时只查表）
#   ① 表值 501 项全对：biome(i) == (i*200 // 25000) & 3
#   ② _biome_of() 与旧公式 (z//25000)&3 全等（含 100000 回绕端点）
#   ③ 世界物化结果与"旧公式镜像物化"逐项相同（段/树/楼的 biome 派生零差异）
#   ④ _terrain_y 沙滩分支与旧公式采样全等
#   ⑤ 源码审计：运行时不再出现 //25000 消费者（审计门禁）
#   ⑥ 性能/规模：INIT 时长、单帧步数、图片块数
import math
import os
import sys
import time
import types

os.environ['SDL_VIDEODRIVER'] = 'dummy'
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

from racing_game import PietInterpreter, PietRuntime, build_game_core

FAILS = []
MAX_Z = 100000
BIOME_KEY0 = 60000


def check(name, cond, detail=''):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f'  ({detail})' if detail else ''), flush=True)
    if not cond:
        FAILS.append(name)


def old_biome(z):
    return (z // 25000) & 3


def mirror_materialize(p):
    """M5 之前的物化逻辑（biome 用 Python 公式），与新代码对拍"""
    segs = []
    for i in range(p.get(51999, 0)):
        z0 = i * 200
        segs.append((p.get(50000 + i), p.get(50600 + i), z0,
                     p.get(50000 + i + 1), p.get(50600 + i + 1), z0 + 200,
                     old_biome(z0)))
    trees = []
    for a_i in range(167):
        i = a_i * 3
        z = i * 200 + 100
        palm = -1 if old_biome(z) == 1 else 0
        trees.append((p.get(55000 + i) - 180, p.get(56000 + i), z, palm))
        trees.append((p.get(55000 + i) + 180, p.get(56000 + i), z, palm))
    blds = []
    zslot = 0
    for i in range(0, 500, 2):
        z = i * 200 + 100
        biome = old_biome(z)
        if biome == 1:
            continue
        cx_t = p.get(55000 + i)
        base = 65000 + zslot * 8
        d, h, w, u = p.get(base), p.get(base + 2), p.get(base + 4), p.get(base + 6)
        for side in (-1, 1):
            x = cx_t + side * (320 + u)
            blds.append((x, z, w, h, d, side, {0: 0, 2: 2, 3: 4}[biome]))
            if biome == 2:
                blds.append((x + side * 20, z, 10, 150, 10, side, 3))
        zslot += 1
    return segs, trees, blds


def terrain_old(x, z, cy, cx):
    """M5 之前的 _terrain_y（biome 用 Python 公式）"""
    d = abs(x - cx)
    if d <= 162:
        return cy
    if old_biome(z) == 1 and x > cx:
        return cy - 14.0 * min(1.0, (d - 162) / 138.0)
    rise = d - 162
    h = min(rise * 0.3, 55) * (1.0 + 0.45 * math.sin(d * 0.011 + z * 0.0016))
    if d > 900:
        h += min((d - 900) * 0.18, 90) * (1.0 + 0.4 * math.sin(d * 0.005 - z * 0.0009))
    return cy + h


def main():
    print('== M5 生物群系表进 Piet 验证 ==', flush=True)
    t0 = time.time()
    img, _, _, _ = build_game_core()
    t_compile = time.time() - t0
    print(f'  图片: {img.size}  编译耗时 {t_compile:.1f}s', flush=True)
    path = os.path.join(HERE, '_m5_core.png')
    img.save(path)

    vm = PietInterpreter(path, codel_size=1, halt_on_backward=False, max_steps=4_000_000)
    vm.reset()
    t0 = time.time()
    cmds1 = vm.run()
    t_init = time.time() - t0
    p = vm.runtime['persist']
    check('INIT 标志置位', p.get(69003) == 1)
    check('帧1 产出 swap', any(c[0] == 'swap' for c in cmds1))
    check('INIT 时长 ≤ 2.0s', t_init <= 2.0, f'{t_init:.2f}s')
    print(f'  图片块数: {len(vm.blocks)}', flush=True)

    # ── ① 生物群系表 501 项全对 ──
    bad = [i for i in range(501) if p.get(BIOME_KEY0 + i) != ((i * 200 // 25000) & 3)]
    check('生物群系表 501 项全对', not bad, f'不符 {bad[:5]}')
    check('表区不与他区重叠（60500 < 65000）', BIOME_KEY0 + 500 < 65000)
    check('表端点 biome(0)=0 biome(500)=0',
          p.get(BIOME_KEY0) == 0 and p.get(BIOME_KEY0 + 500) == 0,
          f"{p.get(BIOME_KEY0)}/{p.get(BIOME_KEY0 + 500)}")

    # ── ② _biome_of() 与旧公式全等 ──
    rt = PietRuntime.__new__(PietRuntime)          # 不触发窗口/GL 初始化
    rt.vm = types.SimpleNamespace(runtime={'persist': p})
    rt._p3030s = p
    bad2 = []
    probes = [0, 1, 199, 200, 25000, 25001, 49999, 50000, 74999, 75000, 99999, 100000]
    probes += list(range(0, MAX_Z, 617))
    for z in probes:
        if rt._biome_of(z) != old_biome(z):
            bad2.append((z, rt._biome_of(z), old_biome(z)))
    check('_biome_of() 与旧公式 (z//25000)&3 全等', not bad2, f'不符 {bad2[:5]}（{len(probes)} 采样）')

    # ── ③ 世界物化零差异 ──
    segs_o, trees_o, blds_o = mirror_materialize(p)
    rt2 = PietRuntime.__new__(PietRuntime)
    rt2.vm = types.SimpleNamespace(runtime={'persist': p})
    rt2._p3030s = p
    rt2._world_ready = False
    rt2.track_data = []; rt2.tree_positions = []; rt2.buildings = []
    rt2._materialize_world()
    check('赛道段物化零差异', rt2.track_data == segs_o,
          f'{len(rt2.track_data)} vs {len(segs_o)}')
    check('树木物化零差异', rt2.tree_positions == trees_o,
          f'{len(rt2.tree_positions)} vs {len(trees_o)}')
    check('建筑物化零差异', rt2.buildings == blds_o,
          f'{len(rt2.buildings)} vs {len(blds_o)}')
    n_palm = sum(1 for t in rt2.tree_positions if t[3] == -1)
    print(f'  [M5] 物化: 段={len(rt2.track_data)} 树={len(rt2.tree_positions)}'
          f' 楼={len(rt2.buildings)} 棕榈={n_palm}', flush=True)

    # ── ④ _terrain_y 沙滩分支全等 ──
    bad3 = []
    for z in range(0, 100001, 2500):
        cy = int(math.sin(z / 600.0) * 40)
        cx = rt._road_cx(z)
        for x in range(-1500, 1501, 60):
            got = rt._terrain_y(x, z, cy)
            ref = terrain_old(x, z, cy, cx)
            if abs(got - ref) > 1e-9:
                bad3.append((x, z, got, ref))
    # 边界外推（>100000）也须安全
    for z in (100000, 100200, 99999):
        _ = rt._terrain_y(3000, z, 0)
    check('_terrain_y 与旧公式采样全等', not bad3, f'不符 {bad3[:3]}（含 z>100000 无异常）')

    # ── ⑤ 源码审计门禁 ──
    with open(os.path.join(HERE, 'racing_game.py'), encoding='utf-8') as f:
        src = f.read()
    for pat in ('z0 // 25000', 'z // 25000', 'self.scroll_z // 25000'):
        check(f'运行时源码已无 `{pat}`', pat not in src)
    check('运行时改用 _biome_of() 查表', src.count('_biome_of') >= 4,
          f"出现 {src.count('_biome_of')} 次")

    # ── ⑥ 单帧性能（沿用 M4 手法：继承 INIT 产物 + _frame_start）──
    vm3 = PietInterpreter(path, codel_size=1, halt_on_backward=False, max_steps=4_000_000)
    vm3._frame_start = vm._frame_start
    vm3.runtime['persist'].update(p)
    vm3.runtime['persist'][2001] = 40000
    vm3.reset(); vm3.run()
    steps = []
    for _ in range(5):
        vm3.reset()
        t1 = time.time()
        vm3.run()
        steps.append((vm3.step_count_last, time.time() - t1))
    avg_step = sum(s for s, _ in steps) / len(steps)
    avg_ms = sum(t for _, t in steps) / len(steps) * 1000
    check('单帧步数 ≤ 8000', avg_step <= 8000, f'平均 {avg_step:.0f} 步 / {avg_ms:.1f}ms（M4 时 4739 步）')

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
