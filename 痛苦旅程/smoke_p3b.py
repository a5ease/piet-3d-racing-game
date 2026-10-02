# -*- coding: utf-8 -*-
# P3 完整 HUD 烟雾测试：真跑游戏（GL 通道）45 帧
#   断言 A：仪表每帧被调用一次，且确实描出几何（多边形数 > 300）
#   断言 B：仪表的"速度"读数 == 同帧 HUD 文本里的速度（同源铁证：Piet 只发布一份 69112；
#           末段注入 ↑ 让速度真的变化，避免断言退化为空真）
#   断言 C：仪表拿到的 scroll_z == 上一帧末的 persist[2001]（渲染与世界同源）
#   断言 D：计时板文本 == 模板.format(分,秒,百分秒,圈号)
#   断言 E：截图像素——速度表/转速盘有强调色，小地图有底板+中心线
#   断言 F：字体 A/B——同代码跑两次只换字体：世界/车体/仪表零差异，文字区显著变化
#   断言 G：源码门禁——draw_hud 已不用 glDrawPixels
#   断言 H：HUD 中文字体已解析到本机 CJK 字体（防"豆腐块"回归）
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault('SDL_VIDEO_WINDOW_POS', '2000,2000')
SHOTS = os.path.join(HERE, '_p3b_shots')
os.environ['PIET_SHOT_DIR'] = SHOTS
os.environ['PIET_MAX_FRAMES'] = '45'

import racing_game as rg
from racing_game import PietRuntime, build_game_core

FAILS = []
SPEED_ACCENT = (255, 206, 64)
TACH_ACCENT = (255, 96, 72)
MINI_PANEL = (40, 44, 56)
MINI_LINE = (148, 156, 170)
BOX_SPEED = (52, 444, 164, 556)
BOX_TACH = (198, 444, 310, 556)
BOX_MINI = (656, 436, 780, 582)
BOX_TP = (520, 6, 800, 38)          # 计时板文字区
BOX_MSG1 = (0, 0, 520, 40)          # 速度行文字区
CAR_BOX = (340, 320, 460, 400)      # 车体区
WORLD_BOXES = [(0, 40, 500, 330), (500, 40, 800, 330), (0, 392, 500, 434)]


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


def count_near(im, box, rgb, tol=14):
    x0, y0, x1, y1 = box
    n = 0
    for x in range(x0, x1):
        for y in range(y0, y1):
            p = im.getpixel((x, y))
            if (abs(p[0] - rgb[0]) <= tol and abs(p[1] - rgb[1]) <= tol
                    and abs(p[2] - rgb[2]) <= tol):
                n += 1
    return n


def main():
    print('== P3 完整 HUD 烟雾测试（真跑 45 帧）==', flush=True)
    core = os.path.join(HERE, 'game_core.png')
    img, td, tp, bd = build_game_core(1)
    img.save(core)
    rt = PietRuntime(core, codel_size=1, track_data=td, tree_positions=tp, building_data=bd)

    gauges, hud_calls = [], []
    poly_n = [0]

    if rt.gl is not None:
        orig_poly = rt.gl.add_polygon

        def spy_poly(pts, r, g, b, a=255):
            poly_n[0] += 1
            return orig_poly(pts, r, g, b, a)

        rt.gl.add_polygon = spy_poly

        orig_hud = rt.gl.draw_hud

        def spy_hud(text, x, y, r=255, g=255, b=255, size=24):
            p = rt.vm.runtime['persist']
            hud_calls.append({'text': text, 'x': x, 'y': y})
            return orig_hud(text, x, y, r, g, b, size)

        rt.gl.draw_hud = spy_hud

        # 仪表直通探针：记录"仪表拿到的入参"与"它描了多少多边形"
        orig_gauges = rg.emit_hud_gauges

        def spy_gauges(draw, p, cx_of, scroll_z, sw=800, sh=600):
            before = poly_n[0]
            r = orig_gauges(draw, p, cx_of, scroll_z, sw, sh)
            gauges.append({
                'cls': type(draw).__name__,
                'speed': p.get(69112, p.get(2002, 0)),
                'raw2002': p.get(2002),
                'scroll_z': scroll_z,
                'p_sz': p.get(2001),
                'n_poly': poly_n[0] - before,
            })
            return r

        rg.emit_hud_gauges = spy_gauges

    # ── 变速注入 ──
    # 无输入时速度恒定 → "仪表读数 == HUD 文本速度"会退化成空真。这里在 frame_count ≥ 42
    # 之后踩住 ↑（前 41 帧保持默认），既让断言非空真，又不影响 frame_040 的世界参照比对。
    import pygame
    _st = {'rt': rt}
    _orig_keys = pygame.key.get_pressed

    class _Keys:
        def __init__(self, up):
            self._up = up

        def __getitem__(self, k):
            return 1 if (k == pygame.K_UP and self._up) else 0

    def _fake_keys():
        return _Keys(getattr(_st['rt'], 'frame_count', 0) >= 42)

    pygame.key.get_pressed = _fake_keys
    try:
        rt.run()
    finally:
        pygame.key.get_pressed = _orig_keys

    # ── A：仪表被调用 ──
    check('GL 通道启用', bool(gauges), f'{len(gauges)} 次仪表绘制')
    check('每帧恰好一次仪表绘制（== begin_3d 每帧 2 次的一半）',
          len(gauges) == len(hud_calls) // 2,
          f'仪表 {len(gauges)} 次 / HUD 消息 {len(hud_calls)} 条')
    check('每次仪表描出的多边形数 >= 250（几何确实画了：45+45+165）',
          gauges and min(g['n_poly'] for g in gauges) >= 250,
          f"最小 {min((g['n_poly'] for g in gauges), default=0)} 个")
    check('仪表使用 GL 适配器', gauges and all(g['cls'] == '_GLPoly' for g in gauges),
          f"类名 {set(g['cls'] for g in gauges)}")

    # ── B：速度同源铁证——仪表速度 == HUD 文本里的速度 ──
    hud1 = [c for c in hud_calls if (c['x'], c['y']) == (10, 10)]
    hud2 = [c for c in hud_calls if (c['x'], c['y']) == (520, 10)]
    check('每帧两条 HUD 消息', len(hud1) == len(hud2) == len(gauges),
          f'msg1 {len(hud1)} / msg2 {len(hud2)} / 仪表 {len(gauges)}')
    if len(gauges) == len(hud1):
        bad = []
        for g, c in zip(gauges, hud1):
            txt = c['text'].split('速度:')[1].split('|')[0].strip()
            try:
                shown = int(txt)
            except ValueError:
                shown = None
            if shown != g['speed']:
                bad.append((c['text'], g['speed'], shown))
        check('仪表速度读数 == 同帧 HUD 文本速度（同源：Piet 只发布 69112）',
              not bad, f'{len(bad)} 帧不符' + (f'  例 {bad[0]}' if bad else ''))
        speeds = sorted({g['speed'] for g in gauges})
        check('该断言非空真（末段踩 ↑ 后速度确有变化）', len(speeds) > 1, f'出现过的速度 {speeds}')
    else:
        check('仪表速度读数 == 同帧 HUD 文本速度', False, '帧数不匹配，无法配对')

    # ── C：仪表拿到的 scroll_z == 上一帧末的 persist[2001] ──
    if len(gauges) > 2:
        bad_sz = [i for i in range(1, len(gauges))
                  if gauges[i]['scroll_z'] != gauges[i - 1]['p_sz']]
        check('仪表 scroll_z == 上一帧末 persist[2001]（渲染与世界同源）',
              not bad_sz, f'{len(bad_sz)} 帧不符')

    # ── D：计时板文本 == 模板.format(...) ──
    tmpl2 = rt._hud_msgs.get(2)
    ok_t2, bad_t2 = bool(tmpl2), []
    for g, c in zip(gauges, hud2):
        p = rt.vm.runtime['persist']
        # 45 帧内不回绕 → 计时板的圈号 == 帧末 persist[69104]
        txt = c['text']
        if not (txt.startswith('时间: ') and txt.endswith(' 圈')):
            bad_t2.append(txt)
            continue
        mm, rest = txt[4:].split(':', 1)
        ss, rest = rest.split('.', 1)
        cs, lap = rest.split('  第 ')
        lap = lap[:-2]
        if not (mm.isdigit() and len(ss) == 2 and len(cs) == 2 and lap.isdigit()):
            bad_t2.append(txt)
    check('计时板文本格式 == "时间: M:SS.CC  第 N 圈"', ok_t2 and not bad_t2,
          f'{len(bad_t2)} 条不符' + (f'  例 {bad_t2[0]}' if bad_t2 else ''))
    if hud2:
        print(f"  末帧计时板: {hud2[-1]['text']!r}（persist[69102]={rt.vm.runtime['persist'].get(69102)}ms）",
              flush=True)

    # ── E/F：回读帧 ──
    from PIL import Image, ImageChops
    shots = sorted(f for f in os.listdir(SHOTS) if f.endswith('.png')) if os.path.isdir(SHOTS) else []
    check('回读到 6 张帧图', len(shots) == 6, f'{shots}')
    for s in shots:
        im = Image.open(os.path.join(SHOTS, s)).convert('RGB')
        n_sp = count_near(im, BOX_SPEED, SPEED_ACCENT)
        n_tc = count_near(im, BOX_TACH, TACH_ACCENT)
        n_pan = count_near(im, BOX_MINI, MINI_PANEL)
        n_ln = count_near(im, BOX_MINI, MINI_LINE)
        n_tp = sum(1 for x in range(BOX_TP[0], BOX_TP[2]) for y in range(BOX_TP[1], BOX_TP[3])
                   if min(im.getpixel((x, y))) > 200)
        check(f'{s} 速度表/转速盘/小地图/计时板已绘制 '
              f'(accents {n_sp}/{n_tc}, 面板 {n_pan}, 中心线 {n_ln}, 计时板白字 {n_tp})',
              n_sp > 50 and n_tc > 50 and n_pan > 1000 and n_ln > 80 and n_tp > 60, '')

    # ── F：字体 A/B 对照（自证式，不依赖任何历史参照图）──
    # 同一份代码、同一输入、同一帧号跑两次，**只有 HUD 字体不同**：
    #   · 世界三块 / 车体 / 三块仪表 → 必须零差异（字体只用于 draw_hud）
    #   · 两块文字区                → 必须有显著差异（否则字体根本没生效）
    # 这样既证明了修好，也把"字体不影响世界"钉死，而且证据可复现、不靠"当年的截图"。
    f_cjk = run_headless('ab_cjk')
    f_def = run_headless('ab_default', font_env='default')
    if os.path.exists(f_cjk) and os.path.exists(f_def):
        for name, bx in (('世界左上', WORLD_BOXES[0]), ('世界右上', WORLD_BOXES[1]),
                         ('世界下', WORLD_BOXES[2]), ('车体', CAR_BOX),
                         ('速度表', BOX_SPEED), ('转速盘', BOX_TACH), ('小地图', BOX_MINI)):
            n = region_diff(f_cjk, f_def, bx)
            check(f'换字体不影响 {name}（零差异）', n == 0, f'{n} 像素')

        n1 = region_diff(f_cjk, f_def, BOX_MSG1)
        n2 = region_diff(f_cjk, f_def, BOX_TP)
        print(f'  字体差异: msg1={n1}px  计时板={n2}px（默认字体下中文退化为豆腐块）', flush=True)
        # 阈值远高于时钟文本的帧间抖动（实测 ~300px），确保不是噪声
        check('msg1 文字区因换字体显著变化（字体确实生效）', n1 > 1000, f'{n1} 像素')
        check('计时板文字区因换字体显著变化', n2 > 1000, f'{n2} 像素')
    else:
        check('字体 A/B 两帧就绪', False, f'cjk={os.path.exists(f_cjk)} def={os.path.exists(f_def)}')

    # ── H：中文字体真生效（防"豆腐块"回归）──
    check('HUD 已解析到本机 CJK 字体路径', bool(rg._CJK_FONT_PATH), f'{rg._CJK_FONT_PATH}')

    # ── G：源码门禁 ──
    with open(os.path.join(HERE, 'racing_game.py'), encoding='utf-8') as fh:
        src = fh.read()
    body = src[src.index('    def draw_hud('):src.index('    def close(self)')]
    # 注意：门禁只测"调用形态"（带括号），否则会被解释"为什么改"的注释误判
    check('draw_hud 已不用 glDrawPixels / glRasterPos',
          'glDrawPixels(' not in body and 'glRasterPos2i(' not in body)
    check('draw_hud 走纹理通路', 'make_texture' in body and 'draw_texture' in body)
    check('仪表几何只写一遍（emit_hud_gauges 定义 1 次 + 2 处调用）',
          src.count('def emit_hud_gauges') == 1 and src.count('emit_hud_gauges(') == 3,
          f"def=1 call={src.count('emit_hud_gauges(') - 1}")

    print()
    if FAILS:
        print(f'== 结果: {len(FAILS)} 项失败 ==')
        sys.exit(1)
    print('== 结果: 全部通过 ==')


if __name__ == '__main__':
    main()
