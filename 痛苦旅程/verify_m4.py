# -*- coding: utf-8 -*-
# M4 门槛测试：相机姿态搬进 Piet（帧段，EXT_DRAW_TRACK 之前）
#   ① 7 个姿态键逐帧与整数镜像逐位一致（含回绕后小 sz＝负角区）
#   ② 顺序铁律：姿态必须用"积分前"的 sz（与渲染同源）——积分后算会差一帧
#   ③ 对旧 float 公式的行为等价（相机值偏差 ≤3，800px 画面即亚像素）
#   ④ 负角路径（sz−150 < 0，sin 宏靠 MOD 归约 + 符号折叠）
#   ⑤ INIT 只跑一次 + 帧段命令一致（沿用 M2/M3 手法）
#   ⑥ 单帧执行步数基准（性能门槛）
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

CAM_KEYS = (69020, 69022, 69024, 69026, 69028, 69030, 69032)
CAM_NAMES = ('cam_x', 'cam_y', 'cam_z', 'look_x', 'look_y', 'look_z', 'car_wy')


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


def _ang(sz, z_off, wavelen):
    """_emit_road 的角度：先算 z=sz+z_off 再 ×1000//λ（VM DIVIDE/DIVIDE 均 floor）"""
    return (sz + z_off) * 1000 // wavelen


def road_cx_piet(sz, z_off):
    return piet_sin(_ang(sz, z_off, 1200)) * 250 // 1000


def road_cy_piet(sz, z_off):
    return piet_sin(_ang(sz, z_off, 600)) * 40 // 1000


def cam_pose_piet(p, sz):
    """emit_cam_pose 的逐位镜像（sz = 该帧积分前的 persist[2001]）"""
    cam_x = road_cx_piet(sz, -150)
    cam_y = road_cy_piet(sz, -150) + p.get(3039, 80)
    cam_z = sz - 220
    look_x = road_cx_piet(sz, 400)
    look_y = cam_y - 12
    look_z = sz + 400
    car_wy = road_cy_piet(sz, 0)
    return dict(zip(CAM_KEYS, (cam_x, cam_y, cam_z, look_x, look_y, look_z, car_wy)))


def cam_pose_float(sz, cam_h):
    """M4 之前的 Python 公式（_draw_track_gl 原样），行为等价参考"""
    cam_x = int(math.sin((sz - 150) / 1200.0) * 250)
    cam_y = int(math.sin((sz - 150) / 600.0) * 40) + cam_h
    cam_z = sz - 220
    look_x = int(math.sin((sz + 400) / 1200.0) * 250)
    look_y = cam_y - 12
    look_z = sz + 400
    car_wy = int(math.sin(sz / 600.0) * 40)
    return dict(zip(CAM_KEYS, (cam_x, cam_y, cam_z, look_x, look_y, look_z, car_wy)))


def main():
    print('== M4 相机姿态进 Piet 验证 ==', flush=True)
    t0 = time.time()
    img, _, _, _ = build_game_core()
    print(f'  图片: {img.size}  编译耗时 {time.time()-t0:.1f}s', flush=True)
    path = os.path.join(HERE, '_m4_core.png')
    img.save(path)

    vm = PietInterpreter(path, codel_size=1, halt_on_backward=False, max_steps=4_000_000)
    vm.reset()
    cmds1 = vm.run()
    p = vm.runtime['persist']
    check('INIT 标志置位', p.get(69003) == 1)
    check('帧1 产出 swap', any(c[0] == 'swap' for c in cmds1))

    base_speed = p.get(3036, 8)
    cam_h = p.get(3039, 80)

    # ── ① 逐帧对拍：姿态用【积分前】的 sz（帧1 前 persist[2001] 为空 → 0）──
    n_frames = 200
    p[2001] = 99999                      # 先逼近回绕，让后续帧落在小 sz（负角区）
    z = 99999
    bad = {k: 0 for k in CAM_KEYS}
    for f in range(1, n_frames + 1):
        vm.reset()
        vm.run()
        exp = cam_pose_piet(p, z)
        for k, nm in zip(CAM_KEYS, CAM_NAMES):
            if p.get(k) != exp[k]:
                bad[k] += 1
                if bad[k] <= 2:
                    print(f'    帧{f} {nm} got={p.get(k)} ref={exp[k]}（sz={z}）', flush=True)
        z = (z + base_speed) % MAX_Z
    tot_bad = sum(bad.values())
    check(f'连续 {n_frames} 帧姿态逐位一致', tot_bad == 0,
          f'{tot_bad} 处不符' + (f' 明细 {[(n, c) for n, c in zip(CAM_NAMES, bad.values()) if c]}' if tot_bad else ''))
    check('帧末 persist[2001] 与镜像递推一致', p.get(2001) == z, f'got {p.get(2001)} ref {z}')

    # ── ② 顺序铁律：姿态必须锁定"积分前"的 sz ──
    p[2001] = 50000
    p[2002] = 8
    vm.reset(); vm.run()
    check('积分：50000+8 → 50008', p.get(2001) == 50008, f'got {p.get(2001)}')
    check('cam_z 用积分前 sz（50000−220=49780）', p.get(69024) == 49780, f'got {p.get(69024)}')
    exp = cam_pose_piet(p, 50000)
    check('该帧全部姿态键 == mirror(sz=50000)',
          all(p.get(k) == exp[k] for k in CAM_KEYS),
          f'cam_x got={p.get(69020)} ref={exp[69020]}')
    p[2002] = base_speed

    # ── ③ 行为等价：镜像 vs 旧 float 公式（全 z 扫描）──
    worst = {k: 0 for k in CAM_KEYS}
    n_gt2 = 0
    for sz in range(0, MAX_Z, 7):
        mp = cam_pose_piet(p, sz)
        fp = cam_pose_float(sz, cam_h)
        for k in CAM_KEYS:
            d = abs(mp[k] - fp[k])
            worst[k] = max(worst[k], d)
            if d > 2:
                n_gt2 += 1
    wmax = max(worst.values())
    check('姿态与旧 float 公式偏差 ≤3', wmax <= 3,
          f'最大 {wmax}（' + ' '.join(f'{n}={worst[k]}' for n, k in zip(CAM_NAMES, CAM_KEYS)) + f'；>2 的样本 {n_gt2}')
    check('cam_z / look_z 与旧公式完全一致',
          worst[69024] == 0 and worst[69030] == 0, f'{worst[69024]} / {worst[69030]}')

    # ── ④ 负角路径：sz−150 < 0（sin 宏 MOD 归约 + 符号折叠）──
    neg_bad = []
    for sz in (0, 3, 50, 149, 150):
        p[2001] = sz
        vm.reset(); vm.run()
        exp = cam_pose_piet(p, sz)
        if any(p.get(k) != exp[k] for k in CAM_KEYS):
            neg_bad.append(sz)
    check('负角区（sz−150<0）姿态一致', not neg_bad, f'不符 sz={neg_bad}')
    p[2001] = 0
    vm.reset(); vm.run()
    check('sz=0 cam_x 与旧公式偏差 ≤2',
          abs(p.get(69020) - int(math.sin(-150 / 1200.0) * 250)) <= 2,
          f"piet={p.get(69020)} float={int(math.sin(-150 / 1200.0) * 250)}")

    # ── ⑤ INIT 只跑一次（新 VM 继承 _frame_start，限步内见 swap）──
    #    注意：跳过 INIT 必须【整体继承 persist】。INIT 写的 4100-4108 是 sin/LCG 数学
    #    常数；缺了 SIN_2PI，sin 宏的 MOD 除零 → VM 返回 0 → 整条 sin 链退化为 0。
    vm2 = PietInterpreter(path, codel_size=1, halt_on_backward=False, max_steps=100_000)
    vm2._frame_start = vm._frame_start
    vm2.runtime['persist'].update(p)          # 继承 INIT 全部产物（常数/渲染常量/表）
    vm2.runtime['persist'][2001] = 12345      # 帧内积分前的位置
    vm2.reset()
    cmds2 = vm2.run()
    check('帧2 限步内见 swap（INIT 未重跑）', any(c[0] == 'swap' for c in cmds2))
    check('帧2 姿态被正确算出（sz=12345）',
          vm2.runtime['persist'].get(69020) == cam_pose_piet(vm2.runtime['persist'], 12345)[69020],
          f"got {vm2.runtime['persist'].get(69020)}")
    # hud 命令的参数是 persist 派生数据（P3 起 = 速度/计时/圈号），按设计逐帧可不同；
    # 一致性检查只对"结构性"命令（窗口/天空/赛道/参与者）有意义。
    check('帧2/帧1 帧段命令一致（排除 persist 派生的 hud）',
          [c for c in cmds1 if c[0] not in ('swap', 'hud')] ==
          [c for c in cmds2 if c[0] not in ('swap', 'hud')])

    # ── ⑥ 性能：单帧执行步数 ──
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
    check('单帧步数 ≤ 8000', avg_step <= 8000, f'平均 {avg_step:.0f} 步 / {avg_ms:.1f}ms（M3 时 3485 步）')

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
