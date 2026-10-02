# -*- coding: utf-8 -*-
# M2 门槛测试：世界生成搬进 Piet（INIT 段）
#   ① persist 结构断言（段数/树数/楼槽数/biome 划分）
#   ② 与 float 版数值偏差 ≤2（赛道/树 x）
#   ③ 建筑槽 LCG 布局与 Python 镜像精确一致（piet_sin 镜像逐位复刻 VM floor 语义）
#   ④ INIT 只跑一次断言（帧2 限步内必须见到 swap；帧段命令与帧1 一致）
#   ⑤ INIT 耗时基准（门槛 ≤2s）
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
    s = x3 * P // C0          # VM DIVIDE = a//b（floor）
    return s * (1 - 2 * f)


def cx_piet(z):
    return piet_sin(z * 1000 // 1200) * 250 // 1000


def cy_piet(z):
    return piet_sin(z * 1000 // 600) * 40 // 1000


def mirror_slots():
    """建筑槽值的 Python 镜像，与 build_game_core 发射顺序逐位一致。
    同 z 两侧共享抽取（DUPLICATE 双写），每 z 返回一组 (d,h,w,u)"""
    seed = 12345

    def draw(n, base=0):
        nonlocal seed
        seed = (seed * 1103515245 + 12345) % 2147483648
        return base + seed % n

    slots = []
    for i in range(0, 500, 2):
        z = i * 200 + 100
        biome = (z // 25000) & 3
        if biome == 1:
            continue
        d = draw(31, 30)
        if biome == 3:
            keep = 1 - (1 if draw(10) > 2 else 0)
            h = draw(31, 20) * keep
            w = draw(51, 30) * keep
        else:
            h = draw(201, 100) if biome == 0 else draw(81, 40)
            w = draw(51, 30)
        u = draw(41)
        slots.append((d, h, w, u))   # 同 z 两侧共享同一组值
    return slots


def expand_buildings(p=None, zs=None):
    """从每组槽值 (d,h,w,u) 推导完整建筑列表（与 _materialize_world 同逻辑）"""
    if p is None:
        # 纯镜像路径：cx 用 piet_sin 镜像
        def cxT(i):
            return cx_piet(i * 200 + 100)
    blds = []
    zs_i = 0
    for i in range(0, 500, 2):
        z = i * 200 + 100
        biome = (z // 25000) & 3
        if biome == 1:
            continue
        cx_t = cx_piet(z)
        d, h, w, u = zs[zs_i]
        zs_i += 1
        for side in (-1, 1):
            x = cx_t + side * (320 + u)
            blds.append((x, z, w, h, d, side, {0: 0, 2: 2, 3: 4}[biome]))
            if biome == 2:
                blds.append((x + side * 20, z, 10, 150, 10, side, 3))
    return blds


def main():
    print('== M2 世界生成 INIT 验证 ==', flush=True)
    t0 = time.time()
    img, _, _, _ = build_game_core()
    print(f'  图片: {img.size}  编译耗时 {time.time()-t0:.1f}s', flush=True)
    path = os.path.join(HERE, '_m2_core.png')
    img.save(path)

    vm = PietInterpreter(path, codel_size=1, halt_on_backward=False, max_steps=4_000_000)
    # ── 帧1：INIT + 帧段 ──
    vm.reset()
    t0 = time.time()
    cmds1 = vm.run()
    t_init = time.time() - t0
    check('INIT 耗时 ≤2s', t_init <= 2.0, f'{t_init:.2f}s')
    p = vm.runtime['persist']
    check('INIT 标志置位', p.get(69003) == 1)
    check('帧1 产出 swap', any(c[0] == 'swap' for c in cmds1))

    # ── 结构断言 ──
    check('段数 500', p.get(51999) == 500, f"got {p.get(51999)}")
    check('建筑槽 1504 键',
          65000 in p and 66496 in p and 65000 + 188 * 8 not in p, '')
    biome_cnt = [0, 0, 0, 0]
    for i in range(500):
        biome_cnt[(i * 200 // 25000) & 3] += 1
    check('biome 划分 125×4', biome_cnt == [125, 125, 125, 125], str(biome_cnt))

    # ── 赛道：与 float 版偏差 ≤2（cx1[i]=cx[i+1] 共享由物化保证，这里查表本身）──
    worst_cx = worst_cy = 0
    for i in range(501):
        z = i * 200
        worst_cx = max(worst_cx, abs(p.get(50000 + i) - int(math.sin(z / 1200.0) * 250)))
        worst_cy = max(worst_cy, abs(p.get(50600 + i) - int(math.sin(z / 600.0) * 40)))
    check('赛道 cx 偏差 ≤2', worst_cx <= 2, f'最大 {worst_cx}')
    check('赛道 cy 偏差 ≤2', worst_cy <= 2, f'最大 {worst_cy}')

    # ── 树（从 T 表推导）：与镜像精确一致 + x 与 float 版偏差 ≤2 ──
    tree_bad = 0
    worst_tree = 0
    n_tree = 0
    for a_i in range(167):
        i = a_i * 3
        z = i * 200 + 100
        cx_t = p.get(55000 + i)
        cy_t = p.get(56000 + i)
        if cy_t != cy_piet(z):
            tree_bad += 1
        for side in (-1, 1):
            x = cx_t + side * 180
            n_tree += 1
            if x != cx_piet(z) + side * 180:
                tree_bad += 1
            worst_tree = max(worst_tree,
                             abs(x - (int(math.sin(z / 1200.0) * 250) + side * 180)))
    check('树数据与镜像一致', tree_bad == 0, f'{tree_bad} 处不符')
    check('树 x 偏差 ≤2', worst_tree <= 2, f'最大 {worst_tree}')
    check('树数 334', n_tree == 334)

    # ── 建筑槽：与镜像逐字段精确一致（persist 布局值主序：每 z 8 键 d,d,h,h,w,w,u,u）──
    ref_zs = mirror_slots()
    slot_bad = 0
    zs_i = 0
    for i in range(0, 500, 2):
        z = i * 200 + 100
        biome = (z // 25000) & 3
        if biome == 1:
            continue
        base = 65000 + zs_i * 8
        got = tuple(p.get(base + j) for j in range(8))
        d, h, w, u = ref_zs[zs_i]
        ref = (d, d, h, h, w, w, u, u)
        if got != ref:
            slot_bad += 1
            if slot_bad <= 3:
                print(f'    z#{i} got={got} ref={ref}', flush=True)
        zs_i += 1
    check('建筑槽与镜像一致', slot_bad == 0, f'{slot_bad} 组不符')
    check('组数 188', len(ref_zs) == 188 and zs_i == 188, f'got {len(ref_zs)}/{zs_i}')
    ref_blds = expand_buildings(zs=ref_zs)
    check('建筑总数（含烟囱）502', len(ref_blds) == 502, f'got {len(ref_blds)}')

    # ── INIT 只跑一次：帧2 限步 10 万，若 INIT 重跑则到不了 swap ──
    vm2 = PietInterpreter(path, codel_size=1, halt_on_backward=False, max_steps=100_000)
    # 学习式跳转依赖帧1 记录的 _frame_start；vm2 模拟帧2，需继承学习状态
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
