# -*- coding: utf-8 -*-
# P4 烟雾测试：真跑游戏（GL 通道）45 帧
#   断言 A：相机 eye/center 逐帧等于 persist[69020~69030]（M4 不回退）
#   断言 B：车轮直通——后轮 _wheel3d 收到的 yaw 必须 == persist[69038]/1000（P4 铁证）
#   断言 C：_car_world 逐帧等于 persist[69010]/69032（M3/M4 不回退）
#   断言 D：HUD 仍按模板+参数绘制（P3 不回退）
#   断言 E：帧非黑且含 HUD 白字
#   断言 F：像素确定性——同代码连跑两次，世界/车体/msg1 逐字节相同，仅计时板有时钟抖动
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault('SDL_VIDEO_WINDOW_POS', '2000,2000')
SHOTS = os.path.join(HERE, '_p4_shots')
os.environ['PIET_SHOT_DIR'] = SHOTS
os.environ['PIET_MAX_FRAMES'] = '45'

from racing_game import (PietRuntime, build_game_core, ROAD_YAW_KEY, ROAD_YAW_SCALE,
                         CAR_STEER_KEY, CAR_SPIN_KEY, CAR_POSE_SCALE)

FAILS = []


AB_SHOTS = os.path.join(HERE, '_ab_shots')


def run_headless(tag, font_env=None, frames='41'):
    """子进程跑一次游戏并回读第 40 帧。
    font_env: None=本机 CJK 字体；'default'=强制回退 Latin-only 默认字体（A/B 对照）。"""
    import subprocess
    d = os.path.join(AB_SHOTS, tag)
    os.makedirs(d, exist_ok=True)
    for f in os.listdir(d):
        os.remove(os.path.join(d, f))
    env = dict(os.environ)
    env['PIET_SHOT_DIR'] = d
    env['PIET_MAX_FRAMES'] = frames
    env['PYTHONIOENCODING'] = 'utf-8'
    if font_env is None:
        env.pop('PIET_HUD_FONT', None)
    else:
        env['PIET_HUD_FONT'] = font_env
    r = subprocess.run([sys.executable, os.path.join(HERE, 'racing_game.py')],
                       cwd=HERE, env=env, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    p = os.path.join(d, 'frame_040.png')
    if r.returncode != 0 or not os.path.exists(p):
        print(f'    [{tag}] 子进程失败 exit={r.returncode}', flush=True)
        print((r.stderr or '')[-800:], flush=True)
    return p


def region_diff(pa, pb, box):
    """两图在给定区域内的差异像素数"""
    from PIL import Image, ImageChops
    a = Image.open(pa).convert('RGB')
    b = Image.open(pb).convert('RGB')
    return sum(ImageChops.difference(a.crop(box), b.crop(box)).convert('L').histogram()[1:])


def check(name, cond, detail=''):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f'  ({detail})' if detail else ''), flush=True)
    if not cond:
        FAILS.append(name)


def main():
    print('== P4 烟雾测试（真跑 45 帧）==', flush=True)
    core = os.path.join(HERE, 'game_core.png')
    img, td, tp, bd = build_game_core(1)
    img.save(core)
    rt = PietRuntime(core, codel_size=1, track_data=td, tree_positions=tp, building_data=bd)

    calls, wheel_calls, hud_calls = [], [], []

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
                'text': text, 'x': x, 'y': y, 'col': (r, g, b),
                'speed': rt.vm.runtime['persist'].get(2002),
                'tmpl': rt._hud_msgs.get(1),
            })
            return orig_hud(text, x, y, r, g, b, size)

        rt.gl.draw_hud = spy_hud

        # 车轮直通探针：_draw_car3d_gl 每帧调 4 次，后两次是后轮（yaw_w == road_yaw）
        orig_wheel = rt._wheel3d

        def spy_wheel(gl, cx, cy, cz, r, half_w, spin, yaw, col, cap_col):
            _p = rt.vm.runtime['persist']
            wheel_calls.append({
                'yaw': yaw, 'spin': spin,
                'p_yaw': _p.get(ROAD_YAW_KEY),
                'p_steer': _p.get(CAR_STEER_KEY),      # P5
                'p_spin': _p.get(CAR_SPIN_KEY),        # P5
            })
            return orig_wheel(gl, cx, cy, cz, r, half_w, spin, yaw, col, cap_col)

        rt._wheel3d = spy_wheel

    rt.run()

    # ── A/B/C：M4/M3 不回退 ──
    check('GL 通道启用', bool(calls), f'{len(calls)} 次 begin_3d')
    bad_eye = [c for c in calls if tuple(c['eye']) != tuple(c['p_cam'])]
    check('相机 eye 逐帧等于 persist[69020/22/24]', not bad_eye, f'{len(bad_eye)}/{len(calls)} 不符')
    bad_cx = [c for c in calls if c['center'][0] != c['p_look_x']]
    check('相机 center.x 逐帧等于 persist[69026]', not bad_cx, f'{len(bad_cx)} 帧不符')
    bad_car = [c for c in calls if c['car_world'] and
               (c['car_world'][0] != c['p_car'][0] or c['car_world'][1] != c['p_car'][1])]
    check('_car_world 逐帧等于 persist[69010]/69032', not bad_car, f'{len(bad_car)} 帧不符')

    # ── B：P4 铁证——后轮 yaw 逐次等于 persist[69038]/1000 ──
    #    begin_3d 每帧 2 次（draw_track + draw_car3d）；P3 起 draw_hud 每帧 2 条消息，
    #    故帧数 = hud 调用数 // 2
    n_frames = len(hud_calls) // 2
    check('车轮被调用（4 个/帧）', len(wheel_calls) == 4 * n_frames,
          f'{len(wheel_calls)} 次 vs 期望 {4 * n_frames}（{n_frames} 帧）')
    rear = wheel_calls[2::4] + wheel_calls[3::4]      # 每帧后两次 = 后轮
    bad_yaw = [w for w in rear if w['yaw'] != w['p_yaw'] / ROAD_YAW_SCALE]
    check('后轮 yaw 逐次等于 persist[69038]/1000（Python 侧零偏航决策）',
          rear and not bad_yaw, f'{len(bad_yaw)}/{len(rear)} 次不符')
    if rear:
        nonz = [w for w in rear if w['p_yaw'] != 0]
        print(f"  偏航样本: {len(rear)} 次后轮调用，非零偏航 {len(nonz)} 次，"
              f"末次 persist={rear[-1]['p_yaw']} mrad → yaw={rear[-1]['yaw']:.4f} rad", flush=True)

    # ── B2：P5 铁证——转向角与轮自转都由 Piet 决定（Python 侧零姿态决策）──
    #    前轮 yaw_w = road_yaw + steer，故 (yaw − road_yaw)·1000 必须逐次 == persist[6202]；
    #    轮自转必须逐次 == persist[6204]/1000。
    front = wheel_calls[0::4] + wheel_calls[1::4]          # 每帧前两次 = 前轮
    bad_front = [w for w in front
                 if abs((w['yaw'] - w['p_yaw'] / ROAD_YAW_SCALE) * CAR_POSE_SCALE
                        - w['p_steer']) > 1e-9]
    check('前轮 yaw−road_yaw 逐次等于 persist[6202]/1000（P5 steer 由 Piet 决定）',
          bool(front) and not bad_front, f'{len(bad_front)}/{len(front)} 次不符')
    bad_spin = [w for w in wheel_calls
                if abs(w['spin'] * CAR_POSE_SCALE - w['p_spin']) > 1e-9]
    check('轮自转逐次等于 persist[6204]/1000（P5 轮自转由 Piet 决定）',
          not bad_spin, f'{len(bad_spin)}/{len(wheel_calls)} 次不符')
    nz_steer = [w for w in wheel_calls if w['p_steer'] != 0]
    nz_spin = [w for w in wheel_calls if w['p_spin'] != 0]
    check('P5 非空真：steer/spin 都有非零样本（否则上面两条无区分力）',
          bool(nz_steer) and bool(nz_spin),
          f'非零 steer {len(nz_steer)}/{len(wheel_calls)}，非零 spin {len(nz_spin)}/{len(wheel_calls)}')
    if wheel_calls:
        print(f"  姿态样本: 末次 steer={wheel_calls[-1]['p_steer']} mrad，"
              f"spin={wheel_calls[-1]['p_spin']} mrad → {wheel_calls[-1]['spin']:.4f} rad", flush=True)

    # ── D：P3 不回退（只看 msg1 速度那条）──
    hud1 = [c for c in hud_calls if (c['x'], c['y']) == (10, 10)]
    check('gl.draw_hud 被调用', bool(hud1), f'{len(hud1)} 次 msg1')
    bad_txt = [c for c in hud1
               if c['tmpl'] is None or c['text'] != c['tmpl'].format(c['speed'])]
    check('HUD 文本 == 模板.format(当时 speed)', not bad_txt, f'{len(bad_txt)} 次不符')

    # ── E/F：回读帧 ──
    from PIL import Image, ImageChops
    shots = sorted(f for f in os.listdir(SHOTS) if f.endswith('.png')) if os.path.isdir(SHOTS) else []
    check('回读到 6 张帧图', len(shots) == 6, f'{shots}')
    for s in shots:
        im = Image.open(os.path.join(SHOTS, s)).convert('RGB')
        cols = im.getcolors(1 << 24)
        top = im.getpixel((400, 2))
        white = sum(1 for x in range(0, 250) for y in range(0, 40)
                    if min(im.getpixel((x, y))) > 200)
        check(f'{s} 非黑/色彩丰富/含 HUD 白字（色数 {len(cols)}，白字 {white}）',
              len(cols) > 200 and top != (0, 0, 0) and white > 60, '')

    # ── F：像素确定性（自证式，不依赖历史参照图）──
    # 原 `_p3_shot40.png` 已不可恢复；改用"同一份代码连跑两次"：
    #   世界三块 / 车体    → 必须零差异（证明渲染确定，没有隐藏的随机/时序依赖）
    #   计时板文字区       → 允许小时钟抖动（文本含毫秒读数，实测 ~300px）
    # `road_yaw 生效`的证明不靠像素，而靠断言 B（后轮 yaw 逐次 == persist[69038]/1000
    # 且非零、非常量）——那是比像素 bbox 更强的直通铁证。
    WORLD_BOXES = [(0, 40, 500, 330), (500, 40, 800, 330), (0, 392, 500, 434)]
    CAR_BOX = (340, 320, 460, 400)
    BOX_TP = (520, 6, 800, 38)
    BOX_MSG1 = (0, 0, 520, 40)
    f1 = run_headless('det_a')
    f2 = run_headless('det_b')
    if os.path.exists(f1) and os.path.exists(f2):
        for name, bx in (('世界左上', WORLD_BOXES[0]), ('世界右上', WORLD_BOXES[1]),
                         ('世界下', WORLD_BOXES[2]), ('车体', CAR_BOX), ('msg1', BOX_MSG1)):
            n = region_diff(f1, f2, bx)
            check(f'两次运行 {name} 逐字节相同（渲染确定）', n == 0, f'{n} 像素')
        n_tp = region_diff(f1, f2, BOX_TP)
        print(f'  计时板区帧间差异 {n_tp}px（时钟文本本质非确定，允许 <800）', flush=True)
        check('计时板仅有时钟抖动（其余全确定）', n_tp < 800, f'{n_tp} 像素')
    else:
        check('确定性 A/B 两帧就绪', False, f'a={os.path.exists(f1)} b={os.path.exists(f2)}')

    print()
    if FAILS:
        print(f'== 结果: {len(FAILS)} 项失败 ==')
        sys.exit(1)
    print('== 结果: 全部通过 ==')


if __name__ == '__main__':
    main()
