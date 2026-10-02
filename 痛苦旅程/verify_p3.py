# -*- coding: utf-8 -*-
# P3 门槛测试：HUD 决策搬进 Piet（EXT_HUD + 文本模板进图片 persist[61000+]）
#   ① 模板字节流 = UTF-8('速度: ') + 0xFF + UTF-8(后缀) + 0
#   ② decode_hud_template() 还原出的格式串与旧 f-string 逐字符相同
#   ③ 对全部速度 0..45：模板 + 参数排版结果 == 旧 Python f-string（行为等价）
#   ④ 帧段确实发出 hud 命令，且参数（x,y,size,r,g,b,msg_id,nargs,arg0）全对
#   ⑤ 顺序铁律：hud 在 draw_track 之后、swap 之前；arg0 读的是【本帧速度更新前】的值
#   ⑥ 源码审计：Python 侧不再硬编码 HUD 字符串
#   ⑦ INIT 只跑一次；单帧步数性能门槛
import os
import sys
import time

os.environ['SDL_VIDEODRIVER'] = 'dummy'
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

from racing_game import (PietInterpreter, PietRuntime, build_game_core,
                         decode_hud_template, HUD_KEY0)
import types

FAILS = []

# 独立转写（用 \u 转义手写，避免与生成器共用同一份字面量而互相掩盖笔误）
PRE = '\u901f\u5ea6: '                                  # "速度: "
SUF = '  |  \u2190 \u2192 \u8f6c\u5411  \u2191\u2193 \u8c03\u901f'   # "  |  ← → 转向  ↑↓ 调速"
EXPECT_TMPL = PRE + '{}' + SUF
EXPECT_BYTES = PRE.encode('utf-8') + b'\xff' + SUF.encode('utf-8')


def old_hud(speed):
    """P3 之前的 Python 硬编码 HUD（行为等价参考）"""
    return '\u901f\u5ea6: {}  |  \u2190 \u2192 \u8f6c\u5411  \u2191\u2193 \u8c03\u901f'.format(speed)


def check(name, cond, detail=''):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f'  ({detail})' if detail else ''), flush=True)
    if not cond:
        FAILS.append(name)


def main():
    print('== P3 HUD 决策进 Piet 验证 ==', flush=True)
    img, _, _, _ = build_game_core()
    path = os.path.join(HERE, '_p3_core.png')
    img.save(path)

    vm = PietInterpreter(path, codel_size=1, halt_on_backward=False, max_steps=4_000_000)
    vm.reset()
    t0 = time.time()
    cmds1 = vm.run()
    t_init = time.time() - t0
    p = vm.runtime['persist']
    check('INIT 标志置位', p.get(69003) == 1)
    check('INIT 时长 ≤ 2.0s', t_init <= 2.0, f'{t_init:.2f}s')

    # ── ① 模板字节流 ──
    raw = bytearray()
    i = HUD_KEY0
    while p.get(i):
        raw.append(p.get(i) & 0xFF)
        i += 1
    check('模板字节流一致（UTF-8 + 0xFF 哨兵）', bytes(raw) == EXPECT_BYTES,
          f'len {len(raw)} vs {len(EXPECT_BYTES)}' + ('' if bytes(raw) == EXPECT_BYTES else f'  got={bytes(raw)!r}'))
    check('0 终止符恰好落位', p.get(HUD_KEY0 + len(EXPECT_BYTES)) == 0)
    check('模板键区不与他区重叠（61000+51 < 65000）', HUD_KEY0 + len(EXPECT_BYTES) < 65000)

    # ── ② 解码还原 + 实际物化接线 ──
    tmpl = decode_hud_template(p, HUD_KEY0)
    check('decode_hud_template 还原格式串', tmpl == EXPECT_TMPL, f'got={tmpl!r}')
    rt = PietRuntime.__new__(PietRuntime)          # 不触发窗口/GL 初始化
    rt.vm = types.SimpleNamespace(runtime={'persist': p})
    rt._p3030s = p
    rt._world_ready = False
    rt.track_data = []; rt.tree_positions = []; rt.buildings = []
    rt._materialize_world()
    check('物化后 _hud_msgs[1] == 期望格式串', rt._hud_msgs.get(1) == EXPECT_TMPL,
          f'got={rt._hud_msgs.get(1)!r}')

    # ── ③ 全速度行为等价 ──
    bad = [s for s in range(46) if tmpl.format(s) != old_hud(s)]
    check('速度 0..45 排版结果 == 旧 f-string', not bad, f'不符 {bad[:5]}')
    print(f'  样例: {tmpl.format(0)!r} / {tmpl.format(45)!r}', flush=True)

    # ── ④ 帧段 hud 命令参数（P3 完整 HUD：msg1 = 速度，msg2 = 计时板）──
    huds = [c for c in cmds1 if c[0] == 'hud']
    check('帧1 恰发出 2 条 hud 命令', len(huds) == 2, f'{len(huds)} 条')
    hc1 = next((c for c in huds if c[7] == 1), None)
    hc2 = next((c for c in huds if c[7] == 2), None)
    check('msg1 参数全对 (10,10,24,255,255,255,nargs=1)',
          hc1 is not None and hc1[1] == 10 and hc1[2] == 10 and hc1[3] == 24 and
          hc1[4] == 255 and hc1[5] == 255 and hc1[6] == 255 and len(hc1[8]) == 1,
          f'{hc1[1:] if hc1 else None}')
    check('msg2 参数全对 (520,10,24,255,255,255,nargs=4)',
          hc2 is not None and hc2[1] == 520 and hc2[2] == 10 and hc2[3] == 24 and
          hc2[4] == 255 and hc2[5] == 255 and hc2[6] == 255 and len(hc2[8]) == 4,
          f'{hc2[1:] if hc2 else None}')

    # ── ⑤ 顺序 + "读本帧更新前的速度" ──
    names = [c[0] for c in cmds1]
    check('hud 在 draw_track 之后',
          names.index('hud') > names.index('draw_track'), f'{names}')
    check('hud 在 swap 之前', names.index('hud') < names.index('swap'), f'{names}')
    p[2001] = 30000
    p[2002] = 7
    vm.runtime['keys'][202] = 1          # 按住 ↑：本帧速度会变 9
    vm.reset()
    cmds2 = vm.run()
    h2 = next(c for c in cmds2 if c[0] == 'hud' and c[7] == 1)
    check('arg0 = 本帧速度更新【前】的值 (7)', h2[8][0] == 7, f'got {h2[8][0]}')
    check('本帧末速度已更新为 9', p.get(2002) == 9, f'got {p.get(2002)}')
    check('HUD 文本与旧版一致（speed=7）',
          tmpl.format(*h2[8]) == old_hud(7), f'{tmpl.format(*h2[8])!r}')
    vm.runtime['keys'][202] = 0
    p[2002] = 8

    # ── ⑥ 源码审计 ──
    with open(os.path.join(HERE, 'racing_game.py'), encoding='utf-8') as f:
        src = f.read()
    check('运行时不再有 f-string HUD 硬编码', '\u901f\u5ea6: {speed}' not in src)
    check('运行时不再直接调 draw_hud(f"...")', 'draw_hud(f"' not in src)
    check('HUD 文本字面量只出现在生成器', src.count(SUF) == 1,
          f"出现 {src.count(SUF)} 次")
    check('EXT_HUD 已注册参数表', 'EXT_HUD: 8' in src)

    # ── ⑦ INIT 只跑一次 + 性能 ──
    vm3 = PietInterpreter(path, codel_size=1, halt_on_backward=False, max_steps=100_000)
    vm3._frame_start = vm._frame_start
    vm3.runtime['persist'].update(p)
    vm3.runtime['persist'][2001] = 12345
    vm3.reset()
    cmds3 = vm3.run()
    check('帧2 限步内见 swap（INIT 未重跑）', any(c[0] == 'swap' for c in cmds3))
    h3 = [c for c in cmds3 if c[0] == 'hud']
    check('帧2 仍发 2 条 hud 命令（msg 1 速度 / msg 2 计时板）',
          len(h3) == 2 and {c[7] for c in h3} == {1, 2}, f'{len(h3)} 条')

    vm4 = PietInterpreter(path, codel_size=1, halt_on_backward=False, max_steps=4_000_000)
    vm4._frame_start = vm._frame_start
    vm4.runtime['persist'].update(p)
    vm4.runtime['persist'][2001] = 40000
    vm4.reset(); vm4.run()
    steps, times = [], []
    for _ in range(5):
        vm4.reset()
        t1 = time.time()
        vm4.run()
        steps.append(vm4.step_count_last)
        times.append(time.time() - t1)
    check('单帧步数 ≤ 8000', sum(steps) / len(steps) <= 8000,
          f'平均 {sum(steps)/len(steps):.0f} 步 / {sum(times)/len(times)*1000:.1f}ms')

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
