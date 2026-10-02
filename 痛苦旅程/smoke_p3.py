# -*- coding: utf-8 -*-
# P3 烟雾测试：真跑游戏（GL 通道）45 帧
#   断言 A：相机 eye/center 逐帧等于 persist[69020~69030]（M4 不回退）
#   断言 B：_car_world 逐帧等于 persist[69010]/69032（M3/M4）
#   断言 C：HUD 直通——gl.draw_hud 收到的文本必须 == 模板.format(当时的 persist[2002])，
#           位置/颜色必须等于 Piet 发出的 (10,10,255,255,255)
#   断言 D：回读帧的 HUD 区域确实出现白色字形像素（真画出来了）
#   断言 E：帧非黑且色彩丰富
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault('SDL_VIDEO_WINDOW_POS', '2000,2000')
SHOTS = os.path.join(HERE, '_p3_shots')
os.environ['PIET_SHOT_DIR'] = SHOTS
os.environ['PIET_MAX_FRAMES'] = '45'

from racing_game import PietRuntime, build_game_core

FAILS = []


def check(name, cond, detail=''):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f'  ({detail})' if detail else ''), flush=True)
    if not cond:
        FAILS.append(name)


def main():
    print('== P3 烟雾测试（真跑 45 帧）==', flush=True)
    core = os.path.join(HERE, 'game_core.png')
    img, td, tp, bd = build_game_core(1)
    img.save(core)
    rt = PietRuntime(core, codel_size=1, track_data=td, tree_positions=tp, building_data=bd)

    calls, hud_calls = [], []

    if rt.gl is not None:
        orig_begin = rt.gl.begin_3d

        def spy_begin(**kw):
            p = rt.vm.runtime['persist']
            calls.append({
                'eye': kw.get('eye'), 'center': kw.get('center'),
                'p_cam': (p.get(69020), p.get(69022), p.get(69024)),
                'p_look_x': p.get(69026),
                'car_world': rt._car_world,
                'p_car': (p.get(69010), p.get(69032)),
            })
            return orig_begin(**kw)

        rt.gl.begin_3d = spy_begin

        orig_hud = rt.gl.draw_hud

        def spy_hud(text, x, y, r=255, g=255, b=255, size=24):
            hud_calls.append({
                'text': text, 'x': x, 'y': y, 'col': (r, g, b), 'size': size,
                'speed': rt.vm.runtime['persist'].get(2002),
                'elapsed': rt.vm.runtime['persist'].get(69102),
                'lap': rt.vm.runtime['persist'].get(69104),
                'tmpl': rt._hud_msgs.get(1),
                'tmpl2': rt._hud_msgs.get(2),
            })
            return orig_hud(text, x, y, r, g, b, size)

        rt.gl.draw_hud = spy_hud

    rt.run()

    check('GL 通道启用', bool(calls), f'{len(calls)} 次 begin_3d')
    bad_eye = [c for c in calls if tuple(c['eye']) != tuple(c['p_cam'])]
    check('相机 eye 逐帧等于 persist[69020/22/24]', not bad_eye, f'{len(bad_eye)}/{len(calls)} 不符')
    bad_cx = [c for c in calls if c['center'][0] != c['p_look_x']]
    check('相机 center.x 逐帧等于 persist[69026]', not bad_cx, f'{len(bad_cx)} 帧不符')
    bad_car = [c for c in calls if c['car_world'] and
               (c['car_world'][0] != c['p_car'][0] or c['car_world'][1] != c['p_car'][1])]
    check('_car_world 逐帧等于 persist[69010]/69032', not bad_car, f'{len(bad_car)} 帧不符')

    # ── HUD 直通（P3-1 起每帧两条消息：msg1 速度在 (10,10)、msg2 计时板在 (520,10)）──
    hud1 = [c for c in hud_calls if (c['x'], c['y']) == (10, 10)]
    hud2 = [c for c in hud_calls if (c['x'], c['y']) == (520, 10)]
    check('gl.draw_hud 被调用', bool(hud_calls), f'{len(hud_calls)} 次')
    check('每帧两条 HUD 消息（速度 + 计时板）', len(hud1) == len(hud2) and bool(hud1),
          f'msg1 {len(hud1)} 次 / msg2 {len(hud2)} 次')
    bad_pos = [c for c in hud1 if c['col'] != (255, 255, 255)]
    check('msg1 位置/颜色 == Piet 发的 (10,10,255,255,255)', not bad_pos,
          f'{len(bad_pos)} 次不符' + (f'  例 {bad_pos[0]}' if bad_pos else ''))
    bad_txt = [c for c in hud1
               if c['tmpl'] is None or c['text'] != c['tmpl'].format(c['speed'])]
    check('msg1 文本 == 模板.format(当时 speed)', not bad_txt,
          f'{len(bad_txt)} 次不符' + (f'  例 {bad_txt[0]}' if bad_txt else ''))
    # 45 帧内 speed=8 走 360 单位，绝不回绕 → 计时板圈号 == persist[69104]
    bad_t2 = [c for c in hud2
              if c['tmpl2'] is None or c['text'] != c['tmpl2'].format(
                  c['elapsed'] // 60000, c['elapsed'] // 1000 % 60,
                  c['elapsed'] // 10 % 100, c['lap'])]
    check('msg2 计时板文本 == 模板.format(分,秒,百分秒,圈号)', not bad_t2,
          f'{len(bad_t2)} 次不符' + (f'  例 {bad_t2[0]}' if bad_t2 else ''))
    if hud_calls:
        print(f"  末帧 HUD: {hud_calls[-1]['text']!r}  "
              f"speed={hud_calls[-1]['speed']}", flush=True)

    # ── 回读帧：HUD 区域白色字形像素 + 非黑 ──
    from PIL import Image
    shots = sorted(f for f in os.listdir(SHOTS) if f.endswith('.png')) if os.path.isdir(SHOTS) else []
    check('回读到 6 张帧图', len(shots) == 6, f'{shots}')
    for s in shots:
        im = Image.open(os.path.join(SHOTS, s)).convert('RGB')
        cols = im.getcolors(1 << 24)
        top = im.getpixel((400, 2))
        # HUD 白字：左上角 (0..250, 0..40) 内的近白像素
        white = sum(1 for x in range(0, 250, 1) for y in range(0, 40, 1)
                    if min(im.getpixel((x, y))) > 200)
        check(f'{s} 非黑/色彩丰富/含 HUD 白字（色数 {len(cols)}，白字像素 {white}）',
              len(cols) > 200 and top != (0, 0, 0) and white > 60, '')

    print()
    if FAILS:
        print(f'== 结果: {len(FAILS)} 项失败 ==')
        sys.exit(1)
    print('== 结果: 全部通过 ==')


if __name__ == '__main__':
    main()
