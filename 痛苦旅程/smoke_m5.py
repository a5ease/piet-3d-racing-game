# -*- coding: utf-8 -*-
# M5 烟雾测试：真跑游戏（GL 通道）45 帧
#   断言 A：相机 eye/center 逐帧等于 persist[69020~69030]（M4 不回退）
#   断言 B：_car_world 逐帧等于 persist[69010]/69032
#   断言 C：天空主题区取址 biome 逐次等于 persist[60000+z//200]（M5：biome 全由表来）
#   断言 D：回读帧非黑且色彩丰富
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault('SDL_VIDEO_WINDOW_POS', '2000,2000')
SHOTS = os.path.join(HERE, '_m5_shots')
os.environ['PIET_SHOT_DIR'] = SHOTS
os.environ['PIET_MAX_FRAMES'] = '45'

from racing_game import PietRuntime, build_game_core

FAILS = []


def check(name, cond, detail=''):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f'  ({detail})' if detail else ''), flush=True)
    if not cond:
        FAILS.append(name)


def main():
    print('== M5 烟雾测试（真跑 45 帧）==', flush=True)
    core = os.path.join(HERE, 'game_core.png')
    img, td, tp, bd = build_game_core(1)
    img.save(core)
    rt = PietRuntime(core, codel_size=1, track_data=td, tree_positions=tp, building_data=bd)

    # ── 相机直通探针（M4 回归）──
    calls = []
    if rt.gl is not None:
        orig_begin = rt.gl.begin_3d

        def spy(**kw):
            p = rt.vm.runtime['persist']
            calls.append({
                'eye': kw.get('eye'), 'center': kw.get('center'),
                'p_cam': (p.get(69020), p.get(69022), p.get(69024)),
                'p_look_x': p.get(69026),
                'car_world': rt._car_world,
                'p_car': (p.get(69010), p.get(69032)),
            })
            return orig_begin(**kw)

        rt.gl.begin_3d = spy

    # ── 生物群系查表探针（M5：每次取址都必须与 Piet 表一致）──
    bio_calls = []
    orig_biome = PietRuntime._biome_of

    def spy_biome(self, z):
        r = orig_biome(self, z)
        i = z // 200
        i = 0 if i < 0 else (500 if i > 500 else i)
        bio_calls.append((z, r, self.vm.runtime['persist'].get(60000 + i)))
        return r

    PietRuntime._biome_of = spy_biome
    try:
        rt.run()
    finally:
        PietRuntime._biome_of = orig_biome

    check('GL 通道启用（否则相机断言无意义）', bool(calls), f'{len(calls)} 次 begin_3d')
    bad_eye = [c for c in calls if tuple(c['eye']) != tuple(c['p_cam'])]
    check('相机 eye 逐帧等于 persist[69020/22/24]', not bad_eye,
          f'{len(bad_eye)}/{len(calls)} 帧不符' + (f'  例 {bad_eye[0]}' if bad_eye else ''))
    bad_cx = [c for c in calls if c['center'][0] != c['p_look_x']]
    check('相机 center.x 逐帧等于 persist[69026]', not bad_cx, f'{len(bad_cx)} 帧不符')
    bad_car = [c for c in calls if c['car_world'] and
               (c['car_world'][0] != c['p_car'][0] or c['car_world'][1] != c['p_car'][1])]
    check('_car_world 逐帧等于 persist[69010]/69032', not bad_car, f'{len(bad_car)} 帧不符')

    check('天空 biome 查表被调用', bool(bio_calls), f'{len(bio_calls)} 次')
    bad_bio = [b for b in bio_calls if b[1] != b[2]]
    check('天空 biome 逐次等于 persist[60000+z//200]', not bad_bio,
          f'{len(bad_bio)}/{len(bio_calls)} 次不符' + (f'  例 {bad_bio[0]}' if bad_bio else ''))
    if calls:
        e, cw = calls[-1]['eye'], calls[-1]['car_world']
        print(f'  末帧 eye={e} car_world={cw}', flush=True)

    # ── 回读帧非黑 ──
    from PIL import Image
    shots = sorted(f for f in os.listdir(SHOTS) if f.endswith('.png')) if os.path.isdir(SHOTS) else []
    check('回读到 6 张帧图', len(shots) == 6, f'{shots}')
    for s in shots:
        im = Image.open(os.path.join(SHOTS, s)).convert('RGB')
        cols = im.getcolors(1 << 24)
        top = im.getpixel((400, 2))
        check(f'{s} 非黑且色彩丰富（色数 {len(cols)}，顶行 {top}）',
              len(cols) > 200 and top != (0, 0, 0), '')

    print()
    if FAILS:
        print(f'== 结果: {len(FAILS)} 项失败 ==')
        sys.exit(1)
    print('== 结果: 全部通过 ==')


if __name__ == '__main__':
    main()
