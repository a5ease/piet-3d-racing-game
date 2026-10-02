# P0 渲染后端严格验证
# 断言清单：
#   1. GL 上下文 + 24 位深度可用
#   2. 2D 正交通道照常工作（原有行为不回归）
#   3. 3D 通道（着色器）：透视投影与软件 _project 逐像素一致
#   4. 深度缓冲生效：先画柱子后画地面（后画者更远），近处柱子仍获胜
#   5. 3D → 2D 通道切换无串扰
#   6. 帧回读非黑（黑屏验证手段本身有效）
# 用法: python verify_gl.py [ff]   （ff = 强制固定管线回退路径）
import os, sys
os.environ['SDL_VIDEO_WINDOW_POS'] = '2000,2000'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if 'ff' in sys.argv:
    os.environ['PIET_FORCE_FF'] = '1'

import pygame
from racing_game import GLRenderer, HAS_GL, _project

W, H = 800, 600
FAILS = []

def check(name, cond, detail=''):
    tag = 'PASS' if cond else 'FAIL'
    print(f'  [{tag}] {name}' + (f'  ({detail})' if detail else ''), flush=True)
    if not cond:
        FAILS.append(name)

def near(px, color, tol=6):
    return all(abs(px[i] - color[i]) <= tol for i in range(3))

def has_near(img, cx, cy, color, tol=6, rad=2):
    """3x3~5x5 邻域内出现目标色（吸收 GPU/软件投影的 ±1px 取整差）"""
    for dy in range(-rad, rad + 1):
        for dx in range(-rad, rad + 1):
            if 0 <= cx + dx < W and 0 <= cy + dy < H and near(img.getpixel((cx + dx, cy + dy)), color, tol):
                return True
    return False

def main():
    print('== P0 渲染后端验证 ==', flush=True)
    check('PyOpenGL 导入 (HAS_GL)', HAS_GL)
    if not HAS_GL:
        sys.exit(1)

    pygame.init()
    pygame.display.gl_set_attribute(pygame.GL_DEPTH_SIZE, 24)
    pygame.display.gl_set_attribute(pygame.GL_DOUBLEBUFFER, 1)
    screen = pygame.display.set_mode((W, H), pygame.OPENGL | pygame.DOUBLEBUF)
    gl = GLRenderer()
    check('GLRenderer.init', gl.init(W, H))
    print(f'  3D 管线: {"着色器 (GLSL 330)" if gl.prog3d else "固定管线回退"}', flush=True)

    depth_bits = 0
    from OpenGL.GL import glGetIntegerv, GL_DEPTH_BITS
    depth_bits = int(glGetIntegerv(GL_DEPTH_BITS))
    check('深度缓冲 ≥ 24bit', depth_bits >= 24, f'{depth_bits} bits')

    # ── 阶段 A：2D 正交通道（原行为回归）──
    gl.clear(0, 0, 0)
    gl.add_tri(100, 100, 300, 100, 100, 300, 255, 0, 0)
    gl.flush()
    img_a = gl.screenshot(os.path.join('verify_shots', f'p0_2d_{"ff" if gl.prog3d == 0 else "shader"}.png'))
    check('2D: 红三角命中', near(img_a.getpixel((150, 150)), (255, 0, 0)))
    check('2D: 其余仍为黑', near(img_a.getpixel((700, 500)), (0, 0, 0)))

    # ── 阶段 B：3D 通道（相机 (0,80,-200) 平视 +z，FOV=250 像素焦距）──
    CAM_Z0, CAM_H, FOV = -200, 80, 250
    gl.clear(0, 0, 0)
    gl.begin_3d(cam_h=CAM_H, fov=FOV, cam_z0=CAM_Z0)
    # 红柱（先画，z 近）：x∈[-10,10], y∈[0,120], z=200
    gl.add_quad3d((-10, 0, 200), (10, 0, 200), (10, 120, 200), (-10, 120, 200), 220, 40, 40)
    # 绿地面（后画，z 远）：y=0, x∈[-150,150], z∈[100,800]
    gl.add_quad3d((-150, 0, 100), (150, 0, 100), (150, 0, 800), (-150, 0, 800), 40, 180, 90)
    gl.flush3d()
    gl.end_3d()
    check('3D 批次已清空', len(gl.batch3d) == 0)

    # 3D → 2D 切换无串扰：叠一个蓝色 2D 小块
    gl.add_quad(0, 0, 60, 0, 60, 60, 0, 60, 30, 60, 255)
    gl.flush()
    img_b = gl.screenshot(os.path.join('verify_shots', f'p0_3d_{"ff" if gl.prog3d == 0 else "shader"}.png'))

    RED, GREEN, BLUE = (220, 40, 40), (40, 180, 90), (30, 60, 255)
    # 深度测试：柱子先画、地面后画，但柱子更近 → 柱子必须获胜
    check('深度缓冲: 近柱遮挡远地面(先画者胜)', has_near(img_b, 400, 320, RED))
    # GPU 透视 ≡ 软件投影：用 _project 算屏幕坐标，GPU 画面在同位置必须是地面色
    for wx, wy, wz, label in [(-60, 0, 300, '地面近中'), (100, 0, 700, '地面右侧远'),
                              (0, 120, 200, '柱顶'), (0, 0, 200, '柱底')]:
        sx, sy = _project(wx, wy, wz, CAM_Z0, CAM_H, FOV, W, H)
        expect = GREEN if wy == 0 and wz != 200 else RED
        check(f'GPU≡软件投影: {label} _project→({sx},{sy})', has_near(img_b, sx, sy, expect),
              f'期望{"绿" if expect == GREEN else "红"}')
    # 负对照
    check('负对照: 天空为黑', near(img_b.getpixel((400, 260)), (0, 0, 0)))
    check('负对照: 地面近缘外为黑', near(img_b.getpixel((400, 400)), (0, 0, 0)))
    # 2D 叠加
    check('3D→2D 切换: 蓝块命中', near(img_b.getpixel((30, 30)), BLUE))

    gl.close()
    pygame.quit()

    print()
    if FAILS:
        print(f'== 结果: {len(FAILS)} 项失败 ==')
        for f in FAILS:
            print('  -', f)
        sys.exit(1)
    print('== 结果: 全部通过 ==')

if __name__ == '__main__':
    os.makedirs('verify_shots', exist_ok=True)
    main()
