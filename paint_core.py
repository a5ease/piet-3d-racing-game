# ============================================================
# Piet 图画创作临时窗口
# 用途：在 game_core.png 的白色背景区自由作画（人工，非 AI）
# 安全保证：
#   1. 色板只提供"安全色"——自动避开 Piet 18 指令色 + 黑白
#   2. 锁定第一行 Y=0 代码条，禁止绘制（防破坏游戏逻辑）
# ============================================================
import os
import math
import random
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk, ImageDraw, ImageChops

_HERE = os.path.dirname(os.path.abspath(__file__))
CORE_PATH = os.path.join(_HERE, 'game_core.png')
BACKUP_PATH = os.path.join(_HERE, 'game_core_backup.png')

# ---------- Piet 的 18 种指令色（近色会导致游戏逻辑改变，必须避开）----------
_PIET = [
    (192,0,0),(255,0,0),(255,192,192),(192,192,0),(255,255,0),(255,255,192),
    (0,192,0),(0,255,0),(192,255,192),(0,192,192),(0,255,255),(192,255,255),
    (0,0,192),(0,0,255),(192,192,255),(192,0,192),(255,0,255),(255,192,255),
]

# 生成器布局用的非 Piet 标记色：实测 game_core.png 中唯一的"非 Piet 且非白"颜色。
# 也纳入保护表，达到三个效果：吸管不会吸到它、色板不会生成它、保存时它绝不被改动。
_CODE_EXTRA = [(74, 110, 112)]


def is_safe(rgb):
    """纯白/纯黑及距离任意 Piet 色太近 -> 不安全"""
    if rgb == (255, 255, 255) or rgb == (0, 0, 0):
        return False
    r, g, b = rgb
    for (pc, pg, pb) in _PIET + _CODE_EXTRA:
        if (r-pc)**2 + (g-pg)**2 + (b-pb)**2 <= 10000:
            return False
    return True

def build_code_mask(img):
    """生成"代码像素掩码"（L 模式，255 = 代码本体）。
    只有 Piet 18 指令色或生成器标记色的像素算代码；用户绘画用的安全色不算。
    因此既锁死代码逻辑，又不会把用户自己的画误当保护区（旧画可自由擦改）。"""
    base = img.convert('RGB')
    blank = Image.new('RGB', base.size, (0, 0, 0))
    union = None
    for col in _PIET + _CODE_EXTRA:
        blank.paste(col, (0, 0, base.size[0], base.size[1]))
        m = ImageChops.difference(base, blank).convert('L').point(
            lambda p: 255 if p == 0 else 0)
        union = m if union is None else ImageChops.lighter(union, m)
    return union


def build_safe_palette(n=32):
    """程序化生成 n 个"安全"颜色（固定种子，保证稳定）"""
    seed = 7
    palette = []
    while len(palette) < n:
        seed += 1
        random.seed(seed)
        rgb = (random.randrange(16, 220),
               random.randrange(16, 220),
               random.randrange(16, 220))
        if is_safe(rgb) and rgb not in palette:
            palette.append(rgb)
    # 确保包含常用明亮灰/白平衡色（仍须安全）
    return palette


class Painter(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Piet 图画创作窗口（安全色板）')
        self.geometry('980x740')

        # 载入原图（RGB），并记录原始尺寸
        self.src = Image.open(CORE_PATH).convert('RGB')
        self.sw, self.sh = self.src.size          # 1803 x 1803（正方形）
        self.edit = self.src.copy()               # 当前编辑画布（源分辨率）
        # 代码像素掩码：只有"真正的代码色"（Piet 18 指令色 + 生成器标记色）才被锁定。
        # 比"非纯白即保护"更精准：既绝不破坏代码逻辑，也不会把用户自己的画
        # 当成不可动区域（旧画可以自由擦改、重画）。
        self.code_mask = build_code_mask(self.src)
        # 创作区起始行 = 代码像素掩码的底部边界（红线位置）；不受用户绘画多少影响。
        bb = self.code_mask.getbbox()
        self.code_bottom_row = bb[3] if bb else 0
        # 画笔禁区（guard_mask）= 红线以上整条带 ∪ 红线以下的代码像素。
        # 红线以上即使存在白色缝隙也不许落笔 —— 手滑也完全接触不到代码区。
        top_band = Image.new('L', (self.sw, self.sh), 0)
        top_band.paste(255, (0, 0, self.sw, self.code_bottom_row))
        self.guard_mask = ImageChops.lighter(self.code_mask, top_band)
        self.backup = None
        if os.path.exists(BACKUP_PATH):
            self.backup = Image.open(BACKUP_PATH).convert('RGB')

        # 显示尺寸（全图适应窗口，宽固定）
        self.disp_side = 700
        # 基础映射：全图时 1 个 canvas 像素对应多少源像素（约 2.57，1803/700）
        self.mul_px2src = self.sh / self.disp_side
        # 视口状态（放大镜）
        self.zoom = 1.0                       # 放大倍数，1=全图；最大到像素级
        self.view_cx = self.sw / 2.0
        self.view_cy = self.sh / 2.0
        self._pan_pt = None                   # 右键平移起点

        # 画笔状态
        self.brush = 0                            # 色板索引，-1=橡皮, -2=吸管, -3=自定义取色
        self.line_w = 2                           # 粗细（canvas像素）
        self.custom_rgb = None                    # 吸管取到的颜色
        self._last_pt = None
        self._photo = None

        # 历史（撤销/重做）
        self.undo_stack = []
        self.redo_stack = []
        self._history_limit = 24

        self._build_ui()
        self._push_history(initial=True)
        self._refresh_display()

        # 提示
        self.status(f'载入 {self.sw}x{self.sh} ｜ 色板全部为安全色')

    # ---------- 界面搭建 ----------
    def _build_ui(self):
        left = ttk.Frame(self)
        left.pack(side=tk.LEFT, padx=6, pady=6)
        right = ttk.Frame(self)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=6, pady=6)

        # 画布
        self.canvas = tk.Canvas(left, width=self.disp_side,
                                height=self.disp_side, bg='#202020',
                                highlightthickness=2)
        self.canvas.pack()
        self.img_item = self.canvas.create_image(0, 0, anchor=tk.NW)
        # 顶部警戒线：标记"代码保护区下边界"（红线，位置自动检测，随缩放/平移）
        self.guard_line = self.canvas.create_line(
            0, 0, self.disp_side, 0, fill='#ff5555', width=2, dash=(4, 2))
        self.guard_txt = self.canvas.create_text(
            4, 4, anchor=tk.NW, fill='#ff5555',
            font=('Microsoft YaHei', 9),
            text=f'红线下方为创作区（代码区底部 y={self.code_bottom_row}）')
        # 画布右上角：实时源坐标指示
        self.coord_item = self.canvas.create_text(
            self.disp_side - 6, 6, anchor=tk.NE,
            fill='#22dd88', font=('Microsoft YaHei', 11, 'bold'),
            text='源坐标 (0, 0)')

        # 绑定鼠标（左键绘制，滚轮缩放，右键平移）
        self.canvas.bind('<Motion>', self._on_motion)
        self.canvas.bind('<Leave>', self._on_leave)
        self.canvas.bind('<ButtonPress-1>', self._on_press)
        self.canvas.bind('<B1-Motion>', self._on_drag)
        self.canvas.bind('<ButtonRelease-1>', self._on_release)
        self.canvas.bind('<MouseWheel>', self._on_wheel)
        self.canvas.bind('<ButtonPress-3>', self._on_pan_start)
        self.canvas.bind('<B3-Motion>', self._on_pan_move)
        self.canvas.bind('<ButtonRelease-3>', self._on_pan_end)
        self.bind('<Control-z>', lambda e: self._undo())
        self.bind('<Control-y>', lambda e: self._redo())
        self.bind('<Control-s>', lambda e: self._save())   # Ctrl+S 快捷保存
        self.bind('<Control-S>', lambda e: self._save())   # 兼容大写锁定

        # ---- 右侧：工具区 ----
        ttk.Label(right, text='画笔粗细', font=('Microsoft YaHei', 9)).pack(anchor=tk.W)
        self.size_val = tk.IntVar(value=2)
        fw = ttk.Frame(right); fw.pack(anchor=tk.W, pady=2)
        for i, (val, label) in enumerate([(1, '细'), (2, '中'), (4, '粗')]):
            ttk.Radiobutton(fw, text=label, value=val, variable=self.size_val,
                            command=self._sync_brush).grid(row=0, column=i, padx=2)

        # 工具按钮
        btns = ttk.Frame(right); btns.pack(anchor=tk.W, pady=4)
        ttk.Button(btns, text='橡皮擦', command=lambda: self._set_tool(-1)).grid(row=0, column=0, padx=2)
        ttk.Button(btns, text='画笔', command=lambda: self._set_tool(0)).grid(row=0, column=1, padx=2)
        ttk.Button(btns, text='吸管', command=lambda: self._set_tool(-2)).grid(row=0, column=2, padx=2)

        # 撤销/重做
        hbtn = ttk.Frame(right); hbtn.pack(anchor=tk.W)
        ttk.Button(hbtn, text='撤销', command=self._undo).grid(row=0, column=0, padx=2)
        ttk.Button(hbtn, text='重做', command=self._redo).grid(row=0, column=1, padx=2)

        # 视图（放大镜）
        vbtn = ttk.Frame(right); vbtn.pack(anchor=tk.W, pady=2)
        ttk.Button(vbtn, text='放大', command=lambda: self._zoom_around(self.disp_side/2, self.disp_side/2, 1.4)).grid(row=0, column=0, padx=2)
        ttk.Button(vbtn, text='缩小', command=lambda: self._zoom_around(self.disp_side/2, self.disp_side/2, 0.7)).grid(row=0, column=1, padx=2)
        ttk.Button(vbtn, text='全图', command=self._zoom_fit).grid(row=0, column=2, padx=2)

        self.view_lbl = ttk.Label(right, text='')
        self.view_lbl.pack(anchor=tk.W)

        ctrl = ttk.Frame(right); ctrl.pack(anchor=tk.W, pady=2)
        ttk.Button(ctrl, text='白色画布', command=self._clear_white).grid(row=0, column=0, padx=2)
        ttk.Button(ctrl, text='恢复原图', command=self._restore_src).grid(row=0, column=1, padx=2)

        savebtn = tk.Button(right, text='保存到 game_core.png',
                            bg='#4caf50', fg='white', font=('Microsoft YaHei', 11, 'bold'),
                            command=self._save)
        savebtn.pack(fill=tk.X, pady=6)

        ttk.Separator(right).pack(fill=tk.X, pady=4)
        ttk.Label(right, text='可用颜色（全部为安全色）',
                  font=('Microsoft YaHei', 9, 'bold')).pack(anchor=tk.W)
        # 自定义色按钮：把吸管取到的颜色作为画笔（点击即以该色作画）
        self.custom_btn = tk.Button(right, text='自定义色（取色）',
                                    width=18, bg='#808080',
                                    command=lambda: self._set_tool(-3))
        self.custom_btn.pack(pady=(2, 4))
        self._refresh_custom()

        # 色板（网格按钮）
        self.palette = build_safe_palette(32)
        pal = ttk.Frame(right)
        pal.pack(anchor=tk.W)
        cols = 5
        self.pal_btns = []
        for i, rgb in enumerate(self.palette):
            r, g, b = rgb
            hexcol = '#%02x%02x%02x' % (r, g, b)
            b = tk.Button(pal, width=3, height=1, bg=hexcol,
                          relief=tk.RIDGE, borderwidth=2,
                          command=lambda idx=i: self._set_tool(idx))
            b.grid(row=i // cols, column=i % cols, padx=2, pady=2)
            b.bind('<Enter>',
                   lambda e, rgb=rgb: self.status('颜色 RGB(%d,%d,%d)' % rgb))
            self.pal_btns.append(b)

        ttk.Label(right, text='选中画笔后点色板换色，拖动画布作画',
                  font=('Microsoft YaHei', 8), foreground='#666').pack(pady=(8, 0))

        # 状态栏
        self.status_var = tk.StringVar()
        ttk.Label(self, textvariable=self.status_var,
                  font=('Microsoft YaHei', 9), foreground='#444').pack(side=tk.BOTTOM, anchor=tk.W, padx=8)

        self._refresh_pal_border()

    # ---------- 工具逻辑 ----------
    def _set_tool(self, idx):
        self.brush = idx
        self._refresh_pal_border()

    def _sync_brush(self, *a):
        self.line_w = self.size_val.get()
        if self.brush >= 0:
            self.status('画笔粗细=' + str(self.line_w))

    def _refresh_pal_border(self):
        for i, btn in enumerate(self.pal_btns):
            btn.config(relief=tk.RIDGE if i != self.brush else tk.SOLID,
                       borderwidth=4 if i == self.brush else 2)

    # ---------- 绘画 ----------
    def _k(self):
        """当前视图：1 个 canvas 像素对应的源像素数（放大后变小）"""
        return self.mul_px2src / self.zoom

    def _to_src(self, cx, cy):
        """canvas 坐标 -> 源像素坐标（按当前视口换算）；锁定首行 y>=1"""
        k = self._k()
        sx = int(self.view_cx + (cx - self.disp_side / 2) * k)
        sy = int(self.view_cy + (cy - self.disp_side / 2) * k)
        sx = max(0, min(self.sw - 1, sx))
        sy = max(1, min(self.sh - 1, sy))      # 锁定 y>=1，绝不碰首行
        return sx, sy

    def _on_motion(self, e):
        """鼠标移动时，在画布右上角显示当前源坐标；进入画笔禁区时变红警示"""
        sx, sy = self._to_src(e.x, e.y)
        if self.guard_mask.getpixel((sx, sy)):
            self.canvas.itemconfig(self.coord_item,
                                   text=f'源坐标 ({sx}, {sy}) ｜ 代码保护区（已锁定）',
                                   fill='#ff5555')
        else:
            self.canvas.itemconfig(self.coord_item,
                                   text=f'源坐标 ({sx}, {sy})', fill='#22dd88')

    def _on_leave(self, e):
        self.canvas.itemconfig(self.coord_item, text='源坐标 (—, —)')

    def _on_press(self, e):
        # 吸管模式：单击只取色，不落笔
        if self.brush == -2:
            self._pick(e.x, e.y)
            return
        self._last_pt = e.x, e.y
        self._paint_to(e.x, e.y)

    def _on_drag(self, e):
        if self._last_pt is None:
            self._last_pt = e.x, e.y
        self._paint_to(e.x, e.y, from_pt=self._last_pt)
        self._last_pt = e.x, e.y

    def _on_release(self, e):
        self._last_pt = None
        self._push_history()     # 每一笔完成即记录快照，供撤销
        self._refresh_display()

    def _pick(self, cx, cy):
        """吸管取色：读该点颜色，若为安全色则设为自定义画笔色并自动切换"""
        sx, sy = self._to_src(cx, cy)
        rgb = self.edit.getpixel((sx, sy))
        if not is_safe(rgb):
            self.status('吸到的是指令色/黑白，已拒绝（避免把代码色画进创作区）')
            return
        self.custom_rgb = rgb
        self.brush = -3                     # 自动切换为自定义色画笔
        self._refresh_pal_border()
        self._refresh_custom()
        self.status('取色 RGB(%d,%d,%d) 已设为画笔颜色' % rgb)

    def _refresh_custom(self):
        """刷新"自定义色"按钮的底色显示"""
        if hasattr(self, 'custom_btn'):
            c = self.custom_rgb or (128, 128, 128)
            self.custom_btn.config(bg='#%02x%02x%02x' % c)

    def _restore_guard(self, box):
        """实时保护：把笔触范围内的画笔禁区（红线以上 + 代码像素）立刻还原为原图。
        有了这一步，手滑画到代码区也"涂不上去"——不必等到保存才回滚。"""
        l, t, r, b = box
        l = max(0, l); r = min(self.sw, r)
        t = max(1, t); b = min(self.sh, b)      # 锁定 y>=1，首行代码永不受碰
        if r <= l or b <= t:
            return
        self.edit.paste(self.src.crop((l, t, r, b)), (l, t),
                        self.guard_mask.crop((l, t, r, b)))

    def _paint_to(self, cx, cy, from_pt=None):
        """在当前源分辨率画布上绘制（橡皮涂回白色 / 画笔涂安全色 / 自定义取色）"""
        d = ImageDraw.Draw(self.edit)
        if self.brush == -1:
            color = (255, 255, 255)         # 橡皮擦
        elif self.brush == -3 and self.custom_rgb:
            color = self.custom_rgb         # 吸管取到的自定义色
        else:
            color = self.palette[self.brush]
        # 笔半径按当前视图系数换算成源像素
        r = max(1, int(self.line_w * self._k() / 2))
        if from_pt:
            x0, y0 = self._to_src(*from_pt)
            x1, y1 = self._to_src(cx, cy)
            d.line([(x0, y0), (x1, y1)], fill=color, width=r * 2)
            box = (min(x0, x1) - r, min(y0, y1) - r,
                   max(x0, x1) + r, max(y0, y1) + r)
        else:
            x, y = self._to_src(cx, cy)
            d.ellipse([(x - r, y - r), (x + r, y + r)], fill=color)
            box = (x - r, y - r, x + r, y + r)
        self._restore_guard(box)            # 手滑保护：代码像素当场还原

    # ---------- 历史（撤销/重做） ----------
    def _push_history(self, initial=False):
        # 只在内容确实变化时记录；防止同状态重复入栈
        if self.undo_stack and self.edit is self.undo_stack[-1]:
            pass
        self.undo_stack.append(self.edit.copy())
        if len(self.undo_stack) > self._history_limit:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        if not initial:
            self.status(f'已记录操作（可撤销 {len(self.undo_stack)-1} 步）')

    def _undo(self):
        if len(self.undo_stack) <= 1:
            self.status('没有可撤销的操作')
            return
        self.redo_stack.append(self.edit.copy())
        self.edit = self.undo_stack.pop()
        self._refresh_display()
        self.status(f'已撤销（可撤销 {len(self.undo_stack)-1} 步）')

    def _redo(self):
        if not self.redo_stack:
            self.status('没有可重做的操作')
            return
        self.undo_stack.append(self.edit.copy())
        self.edit = self.redo_stack.pop()
        self._refresh_display()
        self.status(f'已重做（可撤销 {len(self.undo_stack)-1} 步）')

    # ---------- 视图（放大镜 / 平移） ----------
    def _zoom_around(self, cx, cy, factor):
        """以 canvas 点为锚缩放，保持该点对应源位置不飘移"""
        old = self.mul_px2src / self.zoom
        new_cap = self.mul_px2src / 1.15        # 最大放大：约 1:1 像素级
        nz = self.zoom * factor
        nz = max(1.0, min(new_cap, nz))         # clamp 到 [全图, 像素级]
        new = self.mul_px2src / nz
        # 锚点换算：保持 (cx,cy) 处源坐标不变
        srcx = self.view_cx + (cx - self.disp_side / 2) * old
        srcy = self.view_cy + (cy - self.disp_side / 2) * old
        self.zoom = nz
        # 视口中心移动到锚点，使锚点仍在原 canvas 位置
        self.view_cx = srcx - (cx - self.disp_side / 2) * new
        self.view_cy = srcy - (cy - self.disp_side / 2) * new
        self._refresh_display()

    def _zoom_fit(self):
        self.zoom = 1.0
        self.view_cx = self.sw / 2.0
        self.view_cy = self.sh / 2.0
        self._refresh_display()

    def _on_wheel(self, e):
        factor = 1.15 if e.delta > 0 else (1 / 1.15)
        self._zoom_around(e.x, e.y, factor)

    def _on_pan_start(self, e):
        self._pan_pt = e.x, e.y

    def _on_pan_move(self, e):
        if self._pan_pt is None:
            self._pan_pt = e.x, e.y
        dx = e.x - self._pan_pt[0]
        dy = e.y - self._pan_pt[1]
        self._pan_pt = e.x, e.y
        k = self._k()
        self.view_cx -= dx * k
        self.view_cy -= dy * k
        self._refresh_display()

    def _on_pan_end(self, e):
        self._pan_pt = None

    # ---------- 显示 ----------
    def _render_view(self):
        """按当前视口/缩放从 edit 裁出显示图。
        关键：裁出的视口恒为 vw×vw 正方形，超出图幅的部分用背景色补齐，
        绝不做非等比拉伸，避免正方形在边缘处被拉成长方形。"""
        k = self._k()
        vw = self.disp_side * k                 # 视口对应的源宽高
        if self.zoom <= 1.0 or vw >= self.sw * 1.0:
            return self.edit.resize((self.disp_side, self.disp_side), Image.NEAREST)
        vwi = max(1, int(vw))
        x0 = int(self.view_cx - vw / 2)
        y0 = int(self.view_cy - vw / 2)
        # 裁出图幅内的有效区域
        l = max(0, x0); t = max(0, y0)
        r = min(self.sw, x0 + vwi); b = min(self.sh, y0 + vwi)
        box = self.edit.crop((l, t, r, b))
        # 建 vw×vw 画布，深灰底色填充缺失部分，保持正方形尺寸与相对位置
        canvas = Image.new('RGB', (vwi, vwi), (20, 20, 24))
        canvas.paste(box, (l - x0, t - y0))
        return canvas.resize((self.disp_side, self.disp_side), Image.NEAREST)

    def _refresh_display(self):
        disp = self._render_view()
        disp = disp.convert('RGB')
        self._photo = ImageTk.PhotoImage(disp)
        self.canvas.itemconfig(self.img_item, image=self._photo)
        # 红线跟随视口：把代码区底部的源行号换算成当前 canvas 纵坐标
        k = self._k()
        gy = (self.code_bottom_row - self.view_cy) / k + self.disp_side / 2
        if -80 <= gy <= self.disp_side + 80:
            self.canvas.coords(self.guard_line, 0, gy, self.disp_side, gy)
            self.canvas.coords(self.guard_txt, 4, gy + 4)
        else:
            # 视口未包含红线时，把它移出可视区（避免残留横线）
            self.canvas.coords(self.guard_line, 0, -100, self.disp_side, -100)
            self.canvas.coords(self.guard_txt, 4, -100)
        self.view_lbl.config(text=f'放大 x{self.zoom:.2f} ｜ 滚轮缩放 右键平移')

    # ---------- 画布快捷操作 ----------
    def _clear_white(self):
        self._snapshot_before_change()
        new = Image.new('RGB', (self.sw, self.sh), (255, 255, 255))
        # 只保留"代码像素"（Piet 指令色 + 标记色），其余全部清成白布。
        # 因此旧画会被清掉、代码一字不动 —— 这正是"贴合在主代码上的白布"。
        new.paste(self.src, (0, 0), mask=self.code_mask)
        self.edit = new
        self._refresh_display()
        self.status('创作区已清为白布（代码区保留不动），可开始新创作')

    def _restore_src(self):
        self._snapshot_before_change()
        self.edit = self.src.copy()
        self._refresh_display()
        self.status('已恢复为当前 game_core.png')

    def _use_backup(self):
        if self.backup:
            self._snapshot_before_change()
            self.edit = self.backup.copy()
            self._refresh_display()
            self.status('已恢复为备份版 game_core_backup.png')

    def _snapshot_before_change(self):
        """清空类操作前，先把当前状态压入撤销栈"""
        self._push_history()

    # ---------- 保存 ----------
    def _save(self):
        # 保存重建（干净基准法）：以白底 + 代码像素（来自生成基准 src）重建整图。
        #   · 红线以上：只可能剩下代码像素与白，任何越界内容（含上次存下的）一律清掉
        #   · 红线以下：原样保留用户本次编辑的全部内容
        #   · 代码像素最后贴入，优先级最高，永远 100% 原样
        clean = Image.new('RGB', (self.sw, self.sh), (255, 255, 255))
        clean.paste(self.edit.crop((0, self.code_bottom_row, self.sw, self.sh)),
                    (0, self.code_bottom_row))
        clean.paste(self.src, (0, 0), mask=self.code_mask)
        self.edit = clean
        clean.save(CORE_PATH)
        # 保存后自检：读回文件，核对代码像素是否与生成基准（src）逐点一致
        chk = Image.open(CORE_PATH).convert('RGB')
        diff = ImageChops.multiply(
            ImageChops.difference(chk, self.src).convert('L'), self.code_mask)
        worst = diff.getextrema()[1]
        if worst == 0:
            self.status(f'已保存到 {CORE_PATH} ｜ 代码 100% 原样，红线以上已清为空白')
        else:
            self.status(f'警告：代码区出现差异 max={worst}，请勿用其它工具直改此图！')

    def status(self, msg):
        self.status_var.set(msg)


if __name__ == '__main__':
    app = Painter()
    app.mainloop()