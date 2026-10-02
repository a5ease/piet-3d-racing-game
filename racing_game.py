"""纯 Piet 驱动的 3D 赛车游戏
Python =  Piet 的"手"，不决策任何逻辑
"""
import os, sys, math, random
from bisect import bisect_left
from collections import deque
from PIL import Image, ImageChops
import pygame

# OpenGL 渲染引擎
try:
    from OpenGL.GL import *
    from OpenGL.GLU import *
    from ctypes import c_float, c_void_p, sizeof
    HAS_GL = True
except ImportError:
    HAS_GL = False

# ====================================================================
#  Piet 颜色常量
# ====================================================================
COLOR_BY_HL = {
    (0, 0): (192, 0, 0), (0, 1): (255, 0, 0), (0, 2): (255, 192, 192),
    (1, 0): (192, 192, 0), (1, 1): (255, 255, 0), (1, 2): (255, 255, 192),
    (2, 0): (0, 192, 0), (2, 1): (0, 255, 0), (2, 2): (192, 255, 192),
    (3, 0): (0, 192, 192), (3, 1): (0, 255, 255), (3, 2): (192, 255, 255),
    (4, 0): (0, 0, 192), (4, 1): (0, 0, 255), (4, 2): (192, 192, 255),
    (5, 0): (192, 0, 192), (5, 1): (255, 0, 255), (5, 2): (255, 192, 255),
}

INSTR_DELTAS = {
    'NOP': (0, 0), 'PUSH': (0, 1), 'POP': (0, 2),
    'ADD': (1, 0), 'SUBTRACT': (1, 1), 'MULTIPLY': (1, 2),
    'DIVIDE': (2, 0), 'MOD': (2, 1), 'NOT': (2, 2),
    'GREATER': (3, 0), 'POINTER': (3, 1), 'SWITCH': (3, 2),
    'DUPLICATE': (4, 0), 'ROLL': (4, 1), 'IN_N': (4, 2),
    'OUT_N': (5, 0), 'IN_C': (5, 1), 'OUT_C': (5, 2),
}

PIET_COLORS = {
    (255, 0, 0): (0, 1), (192, 0, 0): (0, 0), (255, 192, 192): (0, 2),
    (255, 255, 0): (1, 1), (192, 192, 0): (1, 0), (255, 255, 192): (1, 2),
    (0, 255, 0): (2, 1), (0, 192, 0): (2, 0), (192, 255, 192): (2, 2),
    (0, 255, 255): (3, 1), (0, 192, 192): (3, 0), (192, 255, 255): (3, 2),
    (0, 0, 255): (4, 1), (0, 0, 192): (4, 0), (192, 192, 255): (4, 2),
    (255, 0, 255): (5, 1), (192, 0, 192): (5, 0), (255, 192, 255): (5, 2),
}

INSTRUCTIONS = {
    (0, 0): 'NOP', (0, 1): 'PUSH', (0, 2): 'POP',
    (1, 0): 'ADD', (1, 1): 'SUBTRACT', (1, 2): 'MULTIPLY',
    (2, 0): 'DIVIDE', (2, 1): 'MOD', (2, 2): 'NOT',
    (3, 0): 'GREATER', (3, 1): 'POINTER', (3, 2): 'SWITCH',
    (4, 0): 'DUPLICATE', (4, 1): 'ROLL', (4, 2): 'IN_N',
    (5, 0): 'OUT_N', (5, 1): 'IN_C', (5, 2): 'OUT_C',
}

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
DP_DIRS = [(1, 0), (0, 1), (-1, 0), (0, -1)]

# 扩展指令协议
EXT_MARKER   = 65535

# 绘图指令
EXT_CLEAR    = 1   # 清屏: r, g, b
EXT_RECT     = 2   # 画矩形: x, y, w, h, r, g, b
EXT_TRI      = 3   # 画三角形: x1,y1, x2,y2, x3,y3, r,g,b
EXT_SWAP     = 4   # 刷新画面
EXT_TEXT     = 5   # 画文字: x, y, size, r, g, b

# 窗口/系统控制
EXT_CREATE_WIN = 10  # 创建窗口: w, h
EXT_CLOSE_WIN  = 11  # 关闭窗口
EXT_QUIT_CHECK = 12  # 检查退出事件

# 3D 绘图
EXT_DRAW_TRI_3D = 20  # 画3D三角形: x1,y1,z1, x2,y2,z2, x3,y3,z3, r,g,b

# Phase C: 持久状态
EXT_PUT      = 30  # 存入持久值: key, value
EXT_PEEK     = 35  # 从 persist[key] 推入栈顶（Runtime 填好 persist）

EXT_INIT_ALL = 50  # 批量初始化 persist: addr1,val1, addr2,val2, ..., 0,0 结束
EXT_HANDLE_INPUT    = 40  # 读键盘 → 更新 player_x
EXT_DRAW_PLAYER     = 41  # 绘制玩家赛车
EXT_DRAW_TRACK      = 43  # 根据 scroll_z 动态绘制赛道 + 树木

# M2: INIT 段支持
EXT_WRITE_N      = 51  # 批量顺序写 persist: base, count, v0..v(count-1) → persist[base+i]=vi
EXT_SKIP_IF      = 52  # 条件跳转: flag。flag=1 时 VM 跳到 EXT_FRAME_START 记录的块（学习式）
EXT_FRAME_START  = 53  # 帧段起始标记：首次执行时记录 (块,DP,CC) 供 EXT_SKIP_IF 跳转

# P3: HUD
EXT_HUD          = 54  # 画 HUD 文本: x, y, size, r, g, b, msg_id, nargs, arg0..arg(nargs-1)
                       # 文本模板本身存 persist[61000+]（INIT 由 Piet 写入），此处只发"哪条+参数"


# ====================================================================
#  Piet 汇编器 —— 把指令序列生成 PNG 图片（不变）
# ====================================================================
class PietAssembler:
    def __init__(self, start_color=(0, 0)):
        self.start_color = start_color
        self.code = []

    def emit(self, name, arg=None):
        self.code.append((name, arg))

    def push(self, v):
        self.emit('PUSH', v)

    def push_zero(self):
        """真 0：PUSH 压的是色块尺寸，0 尺寸块不存在，push(0) 实际压 1。
        用 1-1 合成真 0（速度钳位同款约定）。"""
        self.push(1)
        self.push(1)
        self.emit('SUBTRACT')

    def push_compact(self, v):
        """十进制分解 PUSH：确保所有 PUSH 宽度 ≤ 10，杜绝跨行分割
           用 (((高*10 + 中)*10 + 低) 的方式组合；负数 = 0 - |v|"""
        v = int(v)
        if v < 0:
            self.push_zero()
            self.push_compact(-v)
            self.emit('SUBTRACT')
            return
        if v == 0:
            self.push_zero()
            return
        digits = []
        work = v
        while work > 0:
            digits.append(work % 10)
            work //= 10
        # digits = [个位, 十位, 百位, ...]
        # 从高位到低位组合
        self.push(digits[-1])
        for d in reversed(digits[:-1]):
            self.push(10)
            self.emit('MULTIPLY')
            if d != 0:
                self.push(d)
                self.emit('ADD')

    def build_image(self, codel_size=1, square=False, layout=None, canvas=(1803, 1803)):
        # 【Piet 标准语义】PUSH 压入"当前色块"的像素数
        # 所以 widths[i] 与 colors[i] 配对：当前色块宽度 = 它将编码的 PUSH 值
        instr_widths = [max(1, int(arg)) if name == 'PUSH' else 1
                        for name, arg in self.code]
        h, l = self.start_color
        colors = [COLOR_BY_HL[(h, l)]]
        for name, _ in self.code:
            dh, dl = INSTR_DELTAS[name]
            h, l = (h + dh) % 6, (l + dl) % 3
            colors.append(COLOR_BY_HL[(h, l)])
        # widths: 起始色块(宽度=第0个指令的PUSH参数) + 各指令色块 + 结束NOP
        # colors: 起始色块 + 各指令执行后的色块 → 与 widths 一一对应

        if layout in ('serpentine', 'spiral'):
            return self._build_serpentine(self.code, codel_size, canvas)

        # ── 线性布局（所有码块在第一行） ──
        widths = [instr_widths[0]] + instr_widths[1:] + [1]
        total_w = sum(widths)
        if not square:
            # 原始长条：一行
            img = Image.new('RGB', (total_w * codel_size, codel_size), WHITE)
        else:
            # 正方形：第一行放码块，剩余区域白色填充
            side = max(total_w, int(math.ceil(math.sqrt(total_w))))
            img = Image.new('RGB', (side * codel_size, side * codel_size), WHITE)
        px = img.load()
        x = 0
        for w, c in zip(widths, colors):
            for i in range(w * codel_size):
                for y in range(codel_size):
                    px[x + i, y] = c
            x += w * codel_size
        return img

    # ====================================================================
    #  蛇形布局（M0）：画框固定，代码向下生长
    #  - 行宽 = 画框宽；偶数行左→右，奇数行右→左（方向由几何反弹自然形成）
    #  - 行间 G 行黑色掩模，每行只在本行末块左缘开 1 列白色"转弯孔"：
    #    DP 撞行尾黑墙 → 反弹向下 → 白孔直滑落入下一行首块，全程零额外指令
    #  - 偶数行首块须 ≥3 宽：其左上/右上角都不得对准洞列，否则 (up) 反滑回上一行
    #  - 尾块宽 3 独占一行：四角全部避开洞列/被黑墙围死 → 8 次尝试全败 = 停机
    #  - 相邻同色块会合并（破坏 PUSH 值），行间掩模保证跨行不合并；
    #    生成代码禁止发射 NOP（NOP 增量 0 会使相邻块同色合并）
    # ====================================================================
    def _build_serpentine(self, code, codel_size, canvas):
        """牛耕阶梯布局（M0 定稿 v7）——画框固定、代码向下生长、构造上无同色合并。

        Piet 时序（解释器语义，已逐步核对）：转移移动先用当前 DP 找目标块，
        指令在移动成功后执行；PUSH 压"被离开块"的尺寸；POINTER 弹栈顶并
        顺时针旋转 DP。CC 恒为 0（所有转移首试命中，无 SWITCH）。

        行尾转向 = 拼接 4 条指令 [PUSH, POINTER, PUSH, POINTER]（栈净零）：
          右行 (dp=0→1→2)：B1(cx,cy) B2(cx+1,cy) B3(cx+2,cy) 各w1；
            B4(cx+2,cy+1) B5(cx+2,cy+2) 各w1。压1转下、压1转左。
            下一块 C 在 B5 左邻 (cx+1, cy+2)，左行，行距 2。
          左行 (dp=2→1→0)：B1 w3(cx-3..cx-1,cy)；B2 w1(cx-4,cy)；B3 w3(cx-7..cx-5,cy)；
            B4 w1(cx-7,cy+1)；B5 w1(cx-7,cy+2)。压3(2→1 下)、压3(1→0 右)。
            下一块 C 在 B5 右邻 (cx-6, cy+2)，右行，行距 2。

        相邻块皆为序列相邻块（颜色链增量非零 → 不同色），行距 2 保证跨行
        不相邻；同色合并在构造上不可能。程序以 EXT_SWAP 结尾（VM 见 swap
        即停），无需几何停机块。
        """
        W, H = canvas
        Wc, Hc = max(1, W // codel_size), max(1, H // codel_size)

        code = code                       # 就地拼接 self.code
        h0, l0 = self.start_color        # (h,l) = 下一个放置块的链上颜色（增量推进）

        def block_w(entry):
            return max(1, int(entry[1])) if entry[0] == 'PUSH' else 1

        def splice(quad):
            # 只插入指令；链色由放置循环逐块推进（O(1)，替代旧 refresh 全量重算）
            code[placed:placed] = quad

        grid = [[0] * Wc for _ in range(Hc)]
        placements = []                   # (cells, color_rgb)
        cx, cy = 0, 0                     # 锚点：右行=下一块左端；左行=下一块右端
        rightward = True
        placed = 0
        guard = 0

        def place(cells):
            for (tx, ty) in cells:
                if not (0 <= tx < Wc and 0 <= ty < Hc):
                    raise RuntimeError(f'块越界: ({tx},{ty}) cx={cx} cy={cy} '
                                       f'placed={placed} Wc={Wc} Hc={Hc}')
                if grid[ty][tx]:
                    raise RuntimeError(f'块重叠: ({tx},{ty}) placed={placed}')
                grid[ty][tx] = 2
            placements.append((cells, COLOR_BY_HL[(h0, l0)]))

        def advance(instr):
            nonlocal h0, l0
            dh, dl = INSTR_DELTAS[instr]
            h0, l0 = (h0 + dh) % 6, (l0 + dl) % 3

        def turn_right_phase():
            """右行行尾转向：拼 [PUSH1,POINTER,PUSH1,POINTER]（4块），dp 0→1→2。
            B4 下方邻格 (cx+2, cy+2) = 新行首个数学块（循环放置，任意宽兼容），
            其右端即锚点。新行左行，行距 2。"""
            nonlocal cx, cy, rightward, placed
            if cy + 2 > Hc - 1:
                raise RuntimeError('阶梯到底：画框高度装不下剩余代码（画框固定）')
            quad = [('PUSH', 1), ('POINTER', None), ('PUSH', 1), ('POINTER', None)]
            cells4 = [[(cx, cy)], [(cx + 1, cy)], [(cx + 2, cy)], [(cx + 2, cy + 1)]]
            splice(quad)
            for k in range(4):
                place(cells4[k])
                advance(quad[k][0])
            placed += 4
            cx, cy = cx + 2, cy + 2           # 新行左行锚点 = 首块右端
            rightward = False

        def turn_left_phase():
            """左行行尾转向：拼 [PUSH3,POINTER,PUSH3,POINTER]（4块），dp 2→1→0。
            B4 下方邻格 (cx-7, cy+2) = 新行首个数学块（循环放置），
            其左端即锚点。新行右行，行距 2。"""
            nonlocal cx, cy, rightward, placed
            if cy + 2 > Hc - 1:
                raise RuntimeError('阶梯到底：画框高度装不下剩余代码（画框固定）')
            quad = [('PUSH', 3), ('POINTER', None), ('PUSH', 3), ('POINTER', None)]
            cells4 = [[(cx - 3, cy), (cx - 2, cy), (cx - 1, cy)], [(cx - 4, cy)],
                      [(cx - 7, cy), (cx - 6, cy), (cx - 5, cy)],
                      [(cx - 7, cy + 1)]]
            splice(quad)
            for k in range(4):
                place(cells4[k])
                advance(quad[k][0])
            placed += 4
            cx, cy = cx - 7, cy + 2           # 新行右行锚点 = 首块左端
            rightward = True

        while placed < len(code):
            guard += 1
            if guard > max(400000, len(code) * 2):
                raise RuntimeError('阶梯打包未收敛')
            w = block_w(code[placed])
            if rightward:
                fits = (cx + w <= Wc - 3)     # 转向 B3 需 anchor+2 ≤ Wc-1
            else:
                fits = (cx - w >= 7)          # 转向 B3 需 anchor-7 ≥ 0
            if not fits:
                if rightward:
                    turn_right_phase()
                else:
                    turn_left_phase()
                continue
            # 放置数学块
            if rightward:
                cells = [(cx + k, cy) for k in range(w)]
                cx += w
            else:
                cells = [(cx - k, cy) for k in range(w)]
                cx -= w
            place(cells)
            advance(code[placed][0])
            placed += 1

        # ── 帽块：最后一条指令在离开最后一块时执行，须有下一块承接 ──
        #（游戏程序以 swap 结尾：执行 swap 的 OUT_N 后 VM 立即停机，帽块只进不出）
        if rightward:
            cap = (cx, cy)
        else:
            cap = (cx - 1, cy)
        place([cap])

        # ── 绘制 ──
        img = Image.new('RGB', (W, H), WHITE)
        px = img.load()
        for (cells, color) in placements:
            for (tx, ty) in cells:
                for yy in range(ty * codel_size, (ty + 1) * codel_size):
                    for xx in range(tx * codel_size, (tx + 1) * codel_size):
                        px[xx, yy] = color
        return img


# ====================================================================
#  Piet 解释器（增强版）—— 状态式 I/O，完整扩展指令
# ====================================================================
class PietInterpreter:
    def __init__(self, image_path, codel_size=1, halt_on_backward=True, max_steps=500000):
        self.img = Image.open(image_path).convert('RGB')
        self.width, self.height = self.img.size
        self.codel_area = max(1, codel_size * codel_size)
        self.halt_on_backward = halt_on_backward
        self.max_steps = max_steps

        # ===== 运行时状态（由 Python 填充，Piet 通过 IN_N 读取）=====
        self.runtime = {
            'keys': [0] * 512,       # 键盘按键状态
            'ticks': 0,              # 时间（毫秒）
            'mouse_x': 0,            # 鼠标 X
            'mouse_y': 0,            # 鼠标 Y
            'quit': 0,               # 退出标志
            'screen_w': 800,         # 窗口宽
            'screen_h': 600,         # 窗口高
            'persist': {             # Phase C: 跨帧持久状态
                2000: 400,           # player_x（屏幕中心）
                2001: 0,             # player_z
            }
        }

        # ===== 输出的绘图命令列表 =====
        self.draw_cmds = []
        self._stack = []  # 当前执行栈（run 中赋值）

        # ===== 扩展命令状态 =====
        self._ext_state = 0
        self._ext_cmd = 0
        self._ext_args = []
        self._ext_expected = 0
        self._ext_saved_stack = []  # 嵌套扩展指令保存栈
        self.EXT_PARAM_COUNT = {
            EXT_CLEAR: 3,
            EXT_RECT: 7,
            EXT_TRI: 9,
            EXT_SWAP: 0,
            EXT_TEXT: 6,
            EXT_CREATE_WIN: 2,
            EXT_CLOSE_WIN: 0,
            EXT_QUIT_CHECK: 0,
            EXT_DRAW_TRI_3D: 12,
            EXT_PUT: 2,
            EXT_PEEK: 1,
            EXT_INIT_ALL: -1,  # 变长：0,0 终止
            EXT_HANDLE_INPUT: 0,
            EXT_DRAW_PLAYER: 0,
            EXT_DRAW_TRACK: 0,
            EXT_WRITE_N: 2,     # 前 2 参 = base,count；随后动态扩容 count 个值
            EXT_SKIP_IF: 1,
            EXT_FRAME_START: 0,
            EXT_HUD: 8,         # x,y,size,r,g,b,msg_id,nargs；随后动态扩容 nargs 个参数
        }

        # ===== 图片预处理 =====
        w, h = self.width, self.height
        pix = self.img.load()
        self._pix_flat_r = [pix[x, y][0] for y in range(h) for x in range(w)]
        self._pix_flat_g = [pix[x, y][1] for y in range(h) for x in range(w)]
        self._pix_flat_b = [pix[x, y][2] for y in range(h) for x in range(w)]

        self.block_map = [[-1] * w for _ in range(h)]
        self.blocks = []
        self.start_block = None
        self.move_cache = []
        # ===== 单步调试器 =====
        self.debug = False          # 开关：True 时每步打印详细信息
        self.debug_log = []         # 调试日志缓冲区
        self.debug_step_limit = 20  # 最多记录多少步（防止刷屏）
        self.debug_step_count = 0
        # M2: 学习式跳转状态（跨帧保持，reset() 不清除）
        self._frame_start = None    # (块id, dp, cc) —— EXT_FRAME_START 首次执行时记录
        self._pending_jump = None
        self._cur_block = None
        self._analyze_codels()

    # ---------- 内部工具方法 ----------
    def _idx(self, x, y):
        return y * self.width + x

    def _is_white_at(self, x, y):
        idx = self._idx(x, y)
        return (self._pix_flat_r[idx], self._pix_flat_g[idx], self._pix_flat_b[idx]) == WHITE

    def _is_black_at(self, x, y):
        idx = self._idx(x, y)
        return (self._pix_flat_r[idx], self._pix_flat_g[idx], self._pix_flat_b[idx]) == BLACK

    def _get_color_info(self, r, g, b):
        key = (r, g, b)
        if key in PIET_COLORS:
            return PIET_COLORS[key]
        if key == WHITE:
            return None
        if key == BLACK:
            return (-1, -1)
        best, bd = None, float('inf')
        for c, info in PIET_COLORS.items():
            d = (r - c[0])**2 + (g - c[1])**2 + (b - c[2])**2
            if d < bd:
                bd, best = d, info
        return best if bd <= 10000 else None

    def _analyze_codels(self):
        w, h = self.width, self.height
        visited = [[False] * w for _ in range(h)]
        for y in range(h):
            for x in range(w):
                if visited[y][x]:
                    continue
                r = self._pix_flat_r[self._idx(x, y)]
                g = self._pix_flat_g[self._idx(x, y)]
                b = self._pix_flat_b[self._idx(x, y)]
                if (r, g, b) == WHITE or (r, g, b) == BLACK:
                    visited[y][x] = True
                    continue
                info = self._get_color_info(r, g, b)
                if info is None:
                    visited[y][x] = True
                    continue
                bid = len(self.blocks)
                block_pixels = []
                queue = deque([(x, y)])
                visited[y][x] = True
                while queue:
                    cx, cy = queue.popleft()
                    block_pixels.append((cx, cy))
                    self.block_map[cy][cx] = bid
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < w and 0 <= ny < h and not visited[ny][nx]:
                            n_idx = self._idx(nx, ny)
                            if (self._pix_flat_r[n_idx], self._pix_flat_g[n_idx],
                                self._pix_flat_b[n_idx]) == (r, g, b):
                                visited[ny][nx] = True
                                queue.append((nx, ny))
                self.blocks.append({'id': bid, 'hue': info[0], 'light': info[1],
                                    'pixels': block_pixels, 'rgb': (r, g, b),
                                    'count': len(block_pixels)})
        # 找起始块
        for y in range(h):
            for x in range(w):
                if self.block_map[y][x] >= 0:
                    self.start_block = self.block_map[y][x]
                    break
            if self.start_block is not None:
                break
        # 构建移动缓存
        self.move_cache = [[[None for _ in range(2)] for _ in range(4)]
                           for _ in range(len(self.blocks))]
        for bid, blk in enumerate(self.blocks):
            pixels = blk['pixels']
            extremes = {}
            if pixels:
                xs = [p[0] for p in pixels]
                ys = [p[1] for p in pixels]
                extremes[0] = max(xs)
                extremes[1] = max(ys)
                extremes[2] = min(xs)
                extremes[3] = min(ys)
            for dp in range(4):
                if dp in (0, 2):
                    border_val = extremes[dp]
                    cand = [p for p in pixels if p[0] == border_val]
                    ex0 = min(cand, key=lambda p: p[1])
                    ex1 = max(cand, key=lambda p: p[1])
                else:
                    border_val = extremes[dp]
                    cand = [p for p in pixels if p[1] == border_val]
                    ex0 = min(cand, key=lambda p: p[0])
                    ex1 = max(cand, key=lambda p: p[0])
                exit_pixels = {0: ex0, 1: ex1}
                for cc in range(2):
                    ex, ey = exit_pixels[cc]
                    dx, dy = DP_DIRS[dp]
                    nx, ny = ex + dx, ey + dy
                    if not (0 <= nx < w and 0 <= ny < h):
                        self.move_cache[bid][dp][cc] = None
                        continue
                    if self._is_black_at(nx, ny):
                        self.move_cache[bid][dp][cc] = None
                        continue
                    if self._is_white_at(nx, ny):
                        wx, wy = nx, ny
                        found = None
                        while True:
                            wx += dx; wy += dy
                            if not (0 <= wx < w and 0 <= wy < h):
                                break
                            if not self._is_white_at(wx, wy):
                                tbid = self.block_map[wy][wx]
                                if tbid >= 0:
                                    h1 = self.blocks[bid]['hue']
                                    l1 = self.blocks[bid]['light']
                                    h2 = self.blocks[tbid]['hue']
                                    l2 = self.blocks[tbid]['light']
                                    dh = (h2 - h1 + 6) % 6
                                    dl = (l2 - l1 + 3) % 3
                                    found = (tbid, dh, dl)
                                break
                        self.move_cache[bid][dp][cc] = found
                    else:
                        tbid = self.block_map[ny][nx]
                        if tbid < 0 or tbid == bid:
                            self.move_cache[bid][dp][cc] = None
                        else:
                            h1 = self.blocks[bid]['hue']
                            l1 = self.blocks[bid]['light']
                            h2 = self.blocks[tbid]['hue']
                            l2 = self.blocks[tbid]['light']
                            dh = (h2 - h1 + 6) % 6
                            dl = (l2 - l1 + 3) % 3
                            self.move_cache[bid][dp][cc] = (tbid, dh, dl)

    # ---------- 运行时状态读取（Piet 调 IN_N 时触发）----------
    def _read_runtime(self, key):
        """Piet 通过 IN_N 读取系统状态"""
        if 0 <= key < 512:
            return self.runtime['keys'][key]
        if key == 1000:
            return self.runtime['ticks']
        if key == 1001:
            return self.runtime['mouse_x']
        if key == 1002:
            return self.runtime['mouse_y']
        if key == 1003:
            return self.runtime['screen_w']
        if key == 1004:
            return self.runtime['screen_h']
        if key == 1100:
            return self.runtime['quit']
        # Phase C: 持久状态（2000+，扩展到 70000：赛道/树/楼/元信息/INIT 守卫）
        if 2000 <= key <= 70000:
            return self.runtime['persist'].get(key, 0)
        return 0

    # ---------- 重置执行状态（每帧调用）----------
    def reset(self):
        """重置 VM 状态，不清除已分析的色块数据"""
        self.draw_cmds = []
        self._ext_state = 0
        self._ext_cmd = 0
        self._ext_args = []
        self._ext_expected = 0
        self._ext_saved_stack = []
        self._current_dp = 0
        self._current_cc = 0
        self._swap_detected = False
        self._pending_jump = None   # EXT_SKIP_IF 触发的跳转（每帧清空）
        self._cur_block = None
        self.step_count_last = 0    # 上一帧执行步数（M3 性能门槛）

    # ---------- 主执行循环 ----------
    def run(self):
        """运行 Piet 程序，返回绘图命令列表"""
        if self.runtime['ticks'] < 1000:
            print(f"[PietVM] run() 开始, 色块={len(self.blocks)}, start={self.start_block}", flush=True)
        self.draw_cmds = []
        self._ext_state = 0
        self._ext_cmd = 0
        self._ext_args = []
        self._ext_expected = 0
        self._ext_saved_stack = []
        stack = []
        self._stack = stack
        dp, cc = 0, 0
        self._current_dp = 0
        self._current_cc = 0
        current = self.start_block
        codel_area = self.codel_area

        if not self.blocks or self.start_block is None:
            print(f"[PietVM] 无有效色块: blocks={len(self.blocks)}, start={self.start_block}", flush=True)
            return self.draw_cmds

        step_count = 0
        for _step in range(self.max_steps):
            moved = False
            for attempt in range(8):
                res = self.move_cache[current][dp][cc]
                if res is not None:
                    nxt, dh, dl = res
                    if self.halt_on_backward and nxt < current:
                        print(f"[PietVM] halt_on_backward: cur={current} nxt={nxt} step={_step}", flush=True)
                        return self.draw_cmds
                    instr = INSTRUCTIONS.get((dh, dl), 'NOP')
                    block_size = self.blocks[current]['count'] // codel_area
                    # 调试：执行前记录
                    if self.debug and self.debug_step_count < self.debug_step_limit:
                        stack_snapshot = stack[:8]
                        self.debug_log.append(
                            f"[步{step_count}] 色块#{current}(sz={block_size}) "
                            f"DP={dp} CC={cc} → "
                            f"instr={instr} "
                            f"栈=[{','.join(str(s) for s in stack_snapshot)}]"
                        )
                        self.debug_step_count += 1
                    # 同步 DP/CC（含尝试循环里的几何旋转），_execute 里的
                    # POINTER/SWITCH 会在此基础上改写并读回
                    self._current_dp, self._current_cc = dp, cc
                    self._cur_block = current
                    self._execute(instr, stack, block_size)
                    dp, cc = self._current_dp, self._current_cc
                    current = nxt
                    # M2: EXT_SKIP_IF 触发的学习式跳转（一步跳到帧段标记块）
                    if self._pending_jump is not None:
                        current, dp, cc = self._pending_jump
                        self._current_dp, self._current_cc = dp, cc
                        self._pending_jump = None
                    moved = True
                    step_count += 1
                    if self.debug and step_count <= 3:
                        print(f"[PietVM 调试] 步{step_count}: instr={instr} blk={block_size} stack={stack[:5]}", flush=True)
                    break
                cc = 1 - cc
                if attempt % 2 == 1:
                    dp = (dp + 1) % 4
            if not moved:
                if self.runtime['ticks'] < 1000:
                    print(f"[PietVM] 无法移动: cur={current} dp={dp} cc={cc} step={_step}", flush=True)
                break
            # 检测到 SWAP 后提前结束（防止无限循环）
            if self._swap_detected:
                if step_count <= 20:
                    print(f"[PietVM] swap 检测到，提前结束，步数={step_count}", flush=True)
                break
            if _step >= self.max_steps - 1:
                if step_count <= 20:
                    print(f"[PietVM] 达到最大步数限制: {self.max_steps}", flush=True)
        if step_count <= 5 or self.runtime['ticks'] < 1000:
            print(f"[PietVM] 完成: 步数={step_count}, cmd数={len(self.draw_cmds)}", flush=True)
        self.step_count_last = step_count    # M3: 性能门槛取用（单帧执行步数）
        return self.draw_cmds

    # ---------- 指令执行 ----------
    def _execute(self, instr, stack, block_size=1):
        try:
            if instr == 'NOP':
                pass
            elif instr == 'PUSH':
                stack.append(block_size)
            elif instr == 'POP':
                if stack:
                    stack.pop()
            elif instr == 'ADD':
                if len(stack) >= 2:
                    b, a = stack.pop(), stack.pop()
                    stack.append(a + b)
            elif instr == 'SUBTRACT':
                if len(stack) >= 2:
                    b, a = stack.pop(), stack.pop()
                    stack.append(a - b)
            elif instr == 'MULTIPLY':
                if len(stack) >= 2:
                    b, a = stack.pop(), stack.pop()
                    stack.append(a * b)
            elif instr == 'DIVIDE':
                if len(stack) >= 2:
                    b, a = stack.pop(), stack.pop()
                    stack.append(a // b if b != 0 else 0)
            elif instr == 'MOD':
                if len(stack) >= 2:
                    b, a = stack.pop(), stack.pop()
                    stack.append(a % b if b != 0 else 0)
            elif instr == 'NOT':
                if stack:
                    stack.append(1 if stack.pop() == 0 else 0)
            elif instr == 'GREATER':
                if len(stack) >= 2:
                    b, a = stack.pop(), stack.pop()
                    stack.append(1 if a > b else 0)
            elif instr == 'POINTER':
                if stack:
                    self._current_dp = (self._current_dp + stack.pop()) % 4
            elif instr == 'SWITCH':
                if stack:
                    self._current_cc = (self._current_cc + stack.pop()) % 2
            elif instr == 'DUPLICATE':
                if stack:
                    stack.append(stack[-1])
            elif instr == 'ROLL':
                if len(stack) >= 2:
                    count, depth = stack.pop(), stack.pop()
                    if depth > 0 and len(stack) >= depth:
                        top = stack[-depth:]
                        del stack[-depth:]
                        count %= depth
                        stack.extend(top[-count:] + top[:-count] if count else top)
            elif instr == 'IN_N':
                # ===== 状态式输入 =====
                if stack:
                    key = stack.pop()
                    stack.append(self._read_runtime(key))
            elif instr == 'IN_C':
                # 保留，但不用于游戏（可以调试用）
                pass
            elif instr == 'OUT_N':
                # ===== 数字输出 / 扩展指令 =====
                if stack:
                    val = stack.pop()
                    if val == EXT_MARKER:
                        if self._ext_state == 2:
                            # 正在收集参数时遇到新 marker → 嵌套子命令，保存当前状态
                            self._ext_saved_stack.append(
                                (self._ext_cmd, self._ext_args[:], self._ext_expected))
                        self._ext_state = 1
                    elif self._ext_state > 0:
                        self._process_ext(val)
                    else:
                        pass  # 普通数字输出暂时忽略
                else:
                    # 栈为空时尝试输出
                    pass
            elif instr == 'OUT_C':
                # ===== 字符输出 =====
                if stack:
                    v = stack.pop()
                    # 如果是扩展 TEXT 模式，收集字符
                    if self._ext_state == 100:
                        self._ext_text_chars.append(chr(v) if 0 <= v <= 255 else '?')
                        if len(self._ext_text_chars) >= self._ext_text_len:
                            text = ''.join(self._ext_text_chars)
                            self.draw_cmds.append(('text', self._ext_text_x,
                                                   self._ext_text_y, self._ext_text_size,
                                                   self._ext_text_r, self._ext_text_g,
                                                   self._ext_text_b, text))
                            self._ext_state = 0
                    else:
                        pass
        except Exception:
            pass

    # ---------- 扩展指令处理（新状态机）----------
    # _ext_state: 0=普通, 1=已收MARKER等待命令, 2=收参数中
    def _process_ext(self, val):
        if self._ext_state == 0:
            return  # 不应该发生
        if self._ext_state == 1:
            # 第一个值 = 扩展指令编号
            self._ext_cmd = val
            n = self.EXT_PARAM_COUNT.get(val, 0)
            self._ext_args = []
            if n == 0:
                self._exec_ext_cmd()
                self._ext_state = 0
            elif val == EXT_TEXT:
                self._ext_expected = n
                self._ext_state = 2
                self._ext_text_len = 0
            elif n == -1:
                # 变长参数（如 EXT_INIT_ALL）：0,0 终止
                self._ext_expected = -1
                self._ext_state = 2
            else:
                self._ext_expected = n
                self._ext_state = 2
        else:  # _ext_state == 2
            self._ext_args.append(val)
            if self._ext_cmd == EXT_WRITE_N and len(self._ext_args) == 2:
                # base,count 收齐后动态扩容：再收 count 个值
                self._ext_expected = 2 + self._ext_args[1]
            if self._ext_cmd == EXT_HUD and len(self._ext_args) == 8:
                # msg_id,nargs 收齐后动态扩容：再收 nargs 个数值参数
                self._ext_expected = 8 + self._ext_args[7]
            if self._ext_expected == -1:
                # 变长：检测到 0,0 终止符则执行
                if len(self._ext_args) >= 2 and self._ext_args[-2] == 0 and self._ext_args[-1] == 0:
                    self._ext_args = self._ext_args[:-2]  # 去掉终止符
                    self._exec_ext_cmd()
                    if self._ext_saved_stack:
                        self._ext_cmd, self._ext_args, self._ext_expected = \
                            self._ext_saved_stack.pop()
                        self._ext_state = 2
                    else:
                        self._ext_state = 0
            elif len(self._ext_args) >= self._ext_expected:
                self._exec_ext_cmd()
                # 执行完后检查是否有嵌套保存的状态
                if self._ext_saved_stack:
                    self._ext_cmd, self._ext_args, self._ext_expected = \
                        self._ext_saved_stack.pop()
                    self._ext_state = 2  # 恢复为收集参数模式
                else:
                    self._ext_state = 0

    def _exec_ext_cmd(self):
        cmd = self._ext_cmd
        a = self._ext_args
        if cmd == EXT_CLEAR:
            self.draw_cmds.append(('clear', a[0], a[1], a[2]))
        elif cmd == EXT_RECT:
            self.draw_cmds.append(('rect', a[0], a[1], a[2], a[3], a[4], a[5], a[6]))
        elif cmd == EXT_TRI:
            self.draw_cmds.append(('tri', a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8]))
        elif cmd == EXT_SWAP:
            self.draw_cmds.append(('swap',))
            self._swap_detected = True
        elif cmd == EXT_TEXT:
            # TEXT: x, y, size, r, g, b + 字符串(通过 OUT_C 发送)
            if len(a) >= 5:
                self._ext_state = 100          # 进入字符串收集模式
                self._ext_text_x = a[0]
                self._ext_text_y = a[1]
                self._ext_text_size = a[2]
                self._ext_text_r = a[3]
                self._ext_text_g = a[4]
                self._ext_text_b = a[5] if len(a) > 5 else 255
                self._ext_text_chars = []
                self._ext_text_len = 0       # 自动收集直到遇到 0
        elif cmd == EXT_CREATE_WIN:
            self.draw_cmds.append(('create_win', a[0], a[1]))
        elif cmd == EXT_CLOSE_WIN:
            self.draw_cmds.append(('close_win',))
        elif cmd == EXT_QUIT_CHECK:
            self.draw_cmds.append(('quit_check',))
        elif cmd == EXT_DRAW_TRI_3D:
            self.draw_cmds.append(('tri_3d', a[0], a[1], a[2], a[3], a[4], a[5],
                                   a[6], a[7], a[8], a[9], a[10], a[11]))
        elif cmd == EXT_PUT:
            # Phase C: 存入持久值 (key, value)
            key, val = a[0], a[1]
            # 自动钳位 player_x
            if key == 2000:
                val = max(50, min(750, val))
            self.runtime['persist'][key] = val
        elif cmd == EXT_PEEK:
            # 从 persist 读值推入 Piet 栈顶（key 在参数中）
            key = a[0]
            val = self.runtime['persist'].get(key, 0)
            self._stack.append(val)
        elif cmd == EXT_HANDLE_INPUT:
            self.draw_cmds.append(('handle_input',))
        elif cmd == EXT_DRAW_PLAYER:
            self.draw_cmds.append(('draw_player',))
        elif cmd == EXT_DRAW_TRACK:
            self.draw_cmds.append(('draw_track',))
        elif cmd == EXT_INIT_ALL:
            # 批量初始化 persist: addr1,val1, addr2,val2, ...
            for i in range(0, len(a), 2):
                key, val = a[i], a[i+1]
                self.runtime['persist'][key] = val
        elif cmd == EXT_WRITE_N:
            # M2: 顺序批写 persist[base+i] = v_i
            base = a[0]
            for i, v in enumerate(a[2:]):
                self.runtime['persist'][base + i] = v
        elif cmd == EXT_SKIP_IF:
            # M2: flag=1 → 跳到 EXT_FRAME_START 学习记录的 (块,DP,CC)
            # 决策在 Piet（flag 由 persist 算术算出），VM 只机械执行跳转
            if a and a[0] == 1 and self._frame_start is not None:
                self._pending_jump = self._frame_start
        elif cmd == EXT_FRAME_START:
            # M2: 帧段起始标记，首次执行时记录跳转目标
            if self._frame_start is None:
                self._frame_start = (self._cur_block, self._current_dp, self._current_cc)
        elif cmd == EXT_HUD:
            # P3: x,y,size,r,g,b,msg_id,nargs,arg0.. → 渲染命令
            #（文本模板在 persist[61000+]，由 Piet INIT 写入，运行时解码）
            self.draw_cmds.append(('hud', a[0], a[1], a[2], a[3], a[4], a[5], a[6], tuple(a[8:])))
        self._ext_args = []

    def add_ext_arg(self, val):
        """外部添加扩展参数（用于 TEXT 模式）"""
        if self._ext_state == 100:
            self._ext_text_chars.append(chr(val) if 0 <= val <= 255 else '?')
            if val == 0:
                text = ''.join(self._ext_text_chars[:-1])
                self.draw_cmds.append(('text', self._ext_text_x, self._ext_text_y,
                                       self._ext_text_size, self._ext_text_r,
                                       self._ext_text_g, self._ext_text_b, text))
                self._ext_state = 0


# ====================================================================
#  GLRenderer —— OpenGL 批渲染器
#  收集三角形 → 一次性提交 GPU，替代逐段 pygame.draw
# ====================================================================
# ── 4x4 矩阵工具（列主序，与 OpenGL 一致；m[col*4 + row]）──
def _mat4_mul(a, b):
    """a @ b（列主序）"""
    out = [0.0] * 16
    for c in range(4):
        for r in range(4):
            s = 0.0
            for k in range(4):
                s += a[k * 4 + r] * b[c * 4 + k]
            out[c * 4 + r] = s
    return out

def _mat4_perspective_f(fov_px, sw, sh, near, far):
    """由“像素焦距”构造透视投影矩阵。
    fov_px 与软件投影 _project 的 FOV 参数同义：sx = sw/2 + wx*fov/rz。
    因此 GPU 透视与原软件投影逐像素一致，P1 迁移可无缝对照。"""
    f = fov_px / (sh / 2.0)          # = 1/tan(fovy/2)
    aspect = sw / float(sh)
    m = [0.0] * 16
    m[0] = f / aspect                 # = 2*fov_px/sw
    m[5] = f                          # = 2*fov_px/sh
    m[10] = (far + near) / (near - far)
    m[11] = -1.0
    m[14] = 2.0 * far * near / (near - far)
    return m

def _mat4_view(cam_h, cam_z0):
    """相机位于 (0, cam_h, cam_z0)，朝 +z 平视，y 轴向上。
    视图空间：x 不变（世界 +x 仍在屏幕右），z 取反变成 GL 的 -z 朝前。"""
    m = [1, 0, 0, 0,
         0, 1, 0, 0,
         0, 0, -1, 0,
         0, 0, 0, 1]
    m[13] = -float(cam_h)   # y - cam_h
    m[14] = float(cam_z0)   # -(z - cam_z0)
    return m

def _mat4_lookat(eye, center, up=(0, 1, 0)):
    """通用视图矩阵（本引擎约定：世界 +x 保持在屏幕右侧）。
    与 _mat4_view 同一套约定：x 不镜像，前向取反映射到 GL 的 -z。"""
    fx, fy, fz = center[0] - eye[0], center[1] - eye[1], center[2] - eye[2]
    n = math.sqrt(fx*fx + fy*fy + fz*fz) or 1.0
    fx, fy, fz = fx / n, fy / n, fz / n
    # 右向量 s = up × f（up=(0,1,0) 时世界 +x 方向）
    sx, sy, sz = up[1]*fz - up[2]*fy, up[2]*fx - up[0]*fz, up[0]*fy - up[1]*fx
    n2 = math.sqrt(sx*sx + sy*sy + sz*sz) or 1.0
    sx, sy, sz = sx / n2, sy / n2, sz / n2
    # 上向量 u = f × s
    ux, uy, uz = fy*sz - fz*sy, fz*sx - fx*sz, fx*sy - fy*sx
    return [sx, ux, -fx, 0.0,
            sy, uy, -fy, 0.0,
            sz, uz, -fz, 0.0,
            -(sx*eye[0] + sy*eye[1] + sz*eye[2]),
            -(ux*eye[0] + uy*eye[1] + uz*eye[2]),
            (fx*eye[0] + fy*eye[1] + fz*eye[2]),
            1.0]

_3D_VS = """
#version 330
in vec3 a_pos;
in vec4 a_color;
uniform mat4 u_mvp;
out vec4 v_color;
void main() {
    gl_Position = u_mvp * vec4(a_pos, 1.0);
    v_color = a_color;
}
"""

_3D_FS = """
#version 330
in vec4 v_color;
out vec4 o_color;
void main() {
    o_color = v_color;
}
"""


# ====================================================================
#  HUD 字体（呈现层）
#  pygame 的默认字体（Font(None, size)）是 Latin-only：中文全渲染成"豆腐块"。
#  这里只解决"选一个能画中文的字体"——纯光栅化，不参与任何游戏决策。
#  注意：不要用 pygame.font.match_font()，pygame 2.6.1 在 Windows 上枚举注册表
#  字体会抛 TypeError（splitext 收到 int）。必须走显式文件路径。
# ====================================================================
_CJK_FONT_FILES = ('simhei.ttf', 'simsun.ttc', 'Deng.ttf', 'msyh.ttc', 'msyhbd.ttc')


def _find_cjk_font():
    """返回本机首个可用的中文字体路径；都找不到则返回 None（回退默认字体）。

    验证钩子：环境变量 PIET_HUD_FONT=default 时强制返回 None（Latin-only 默认字体）。
    smoke_p3b 用它做 A/B 对照——同一份代码、只有字体不同，世界区必须逐字节相同。
    """
    if os.environ.get('PIET_HUD_FONT') == 'default':
        return None
    windir = os.environ.get('WINDIR') or r'C:\Windows'
    for name in _CJK_FONT_FILES:
        path = os.path.join(windir, 'Fonts', name)
        if os.path.exists(path):
            return path
    return None


_CJK_FONT_PATH = _find_cjk_font()


def _make_font(size):
    """HUD 用字体工厂：优先中文字体，失败回退 pygame 默认。"""
    size = int(size)
    if _CJK_FONT_PATH:
        try:
            return pygame.font.Font(_CJK_FONT_PATH, size)
        except Exception:
            pass
    return pygame.font.Font(None, size)


class GLRenderer:
    """OpenGL 批处理渲染器。
    2D 通道：正交投影 + 固定管线 VBO 批三角形（保持原有行为不变）。
    3D 通道（P0 新增）：世界坐标顶点 + GLSL 着色器透视相机 + 深度缓冲；
    着色器不可用时自动退回固定管线（矩阵 + 客户端指针），
    上下文都建不起来时由 PietRuntime 再退回纯 pygame 2D。"""

    def __init__(self):
        self.ready = False
        self.w = self.h = 0
        self.vbo = 0
        self.vbo3d = 0
        self.batch = []          # 2D 三角形缓存 [x,y, r,g,b,a, ...]
        self.batch3d = []        # 3D 世界坐标三角形缓存 [x,y,z, r,g,b,a, ...]
        self._tex_cache = {}     # 纹理缓存 {name: tex_id}
        self._hud_prev = {}      # P3: HUD 槽位 → 上次上传的 (text, r, g, b)
        self._hud_size = {}      # P3: HUD 槽位 → (w, h)
        self._need_clear = True
        # 3D 通道状态
        self.prog3d = 0          # 着色器程序（0 = 用固定管线渲染 3D）
        self._u_mvp = -1
        self._mvp = None         # (c_float*16)，列主序
        self._proj_m = None      # begin_3d 期间的投影/视图矩阵（CPU 投影用）
        self._view_m = None

    def init(self, w, h):
        """初始化 OpenGL 上下文（2D 正交 + 3D 着色器/深度）"""
        if not HAS_GL:
            return False
        self.w, self.h = w, h
        glViewport(0, 0, w, h)
        self._set_ortho_2d()
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glClearColor(0, 0, 0, 1)
        glClearDepth(1.0)
        glDepthFunc(GL_LEQUAL)

        self.vbo = self._gen_buffer()
        self.vbo3d = self._gen_buffer()
        if not self.vbo or not self.vbo3d:
            return False
        # 着色器管线；失败则 3D 通道退回固定管线（仍可用，不黑屏）
        if os.environ.get('PIET_FORCE_FF'):
            print('[GL] PIET_FORCE_FF=1，3D 通道使用固定管线', flush=True)
        else:
            try:
                self._build_3d_program()
            except Exception as e:
                print(f'[GL] 着色器初始化失败，3D 通道退回固定管线: {e}', flush=True)
                self.prog3d = 0
        self.ready = True
        return True

    def _gen_buffer(self):
        buf = glGenBuffers(1)
        try:
            return int(buf[0])   # 某些环境返回长度 1 数组
        except (TypeError, IndexError):
            return int(buf)      # 标量（含 numpy 0 维标量）

    def _set_ortho_2d(self):
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        glOrtho(0, self.w, self.h, 0, -1, 1)   # 左上角为原点
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()

    def _build_3d_program(self):
        """编译 3D 通道着色器（GLSL 330）；任何一步失败抛异常"""
        def _log_str(log):
            if isinstance(log, bytes):
                return log.decode(errors='replace')
            return str(log or '')

        vs = glCreateShader(GL_VERTEX_SHADER)
        glShaderSource(vs, _3D_VS)
        glCompileShader(vs)
        if not glGetShaderiv(vs, GL_COMPILE_STATUS):
            raise RuntimeError('顶点着色器: ' + _log_str(glGetShaderInfoLog(vs)))
        fs = glCreateShader(GL_FRAGMENT_SHADER)
        glShaderSource(fs, _3D_FS)
        glCompileShader(fs)
        if not glGetShaderiv(fs, GL_COMPILE_STATUS):
            raise RuntimeError('片段着色器: ' + _log_str(glGetShaderInfoLog(fs)))
        prog = glCreateProgram()
        glAttachShader(prog, vs)
        glAttachShader(prog, fs)
        glBindAttribLocation(prog, 0, 'a_pos')
        glBindAttribLocation(prog, 1, 'a_color')
        glLinkProgram(prog)
        if not glGetProgramiv(prog, GL_LINK_STATUS):
            raise RuntimeError('链接: ' + _log_str(glGetProgramInfoLog(prog)))
        glDeleteShader(vs)
        glDeleteShader(fs)
        glUseProgram(prog)
        self.prog3d = prog
        self._u_mvp = glGetUniformLocation(prog, 'u_mvp')
        glUseProgram(0)
        print('[GL] 3D 着色器管线就绪 (GLSL 330)', flush=True)

    def clear(self, r=0, g=0, b=0):
        """清屏（颜色 + 深度，为 3D 通道每帧复位深度缓冲）"""
        glClearColor(r/255, g/255, b/255, 1)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self._need_clear = False

    # ================= 3D 通道（P0 新增） =================
    def begin_3d(self, cam_h=None, fov=None, cam_z0=0.0, near=2.0, far=30000.0,
                 eye=None, center=None):
        """进入 3D 透视通道，开启深度测试。
        fov 用像素焦距语义，与软件投影 _project 的 FOV 参数一致（缺省 250）。
        相机：给 eye/center 用任意视角（追尾相机，center 缺省为 eye 正前方）；
        否则用 (0, cam_h, cam_z0) 平视 +z 的简化相机（cam_h 缺省 80）。"""
        if not self.ready:
            return False
        fov = 250.0 if fov is None else float(fov)
        proj = _mat4_perspective_f(fov, self.w, self.h, near, far)
        if eye is not None:
            eye = (float(eye[0]), float(eye[1]), float(eye[2]))
            if center is None:
                center = (eye[0], eye[1], eye[2] + 100.0)
            view = _mat4_lookat(eye, center)
        else:
            cam_h = 80.0 if cam_h is None else float(cam_h)
            view = _mat4_view(cam_h, cam_z0)
        self._proj_m = proj
        self._view_m = view
        mvp = _mat4_mul(proj, view)
        self._mvp = (c_float * 16)(*mvp)
        glEnable(GL_DEPTH_TEST)
        if not self.prog3d:
            # 固定管线回退：直接加载矩阵
            glMatrixMode(GL_PROJECTION)
            glLoadMatrixd(proj)
            glMatrixMode(GL_MODELVIEW)
            glLoadMatrixd(view)
        return True

    def project_point(self, wx, wy, wz):
        """CPU 侧投影（与 begin_3d 的 GPU 相机严格同一矩阵）→ 屏幕像素 (sx, sy)；
        相机未激活或点在近平面后方时返回 None（车精灵/粒子定位用）"""
        if not self._proj_m or not self._view_m:
            return None
        px_, py_, pz_ = float(wx), float(wy), float(wz)
        v = [0.0] * 4
        for r in range(4):
            v[r] = (self._view_m[r] * px_ + self._view_m[4 + r] * py_
                    + self._view_m[8 + r] * pz_ + self._view_m[12 + r])
        if v[3] == 0:
            return None
        c = [0.0] * 4
        for r in range(4):
            c[r] = (self._proj_m[r] * v[0] + self._proj_m[4 + r] * v[1]
                    + self._proj_m[8 + r] * v[2] + self._proj_m[12 + r] * v[3])
        if c[3] == 0:
            return None
        ndc_x, ndc_y = c[0] / c[3], c[1] / c[3]
        return ((ndc_x + 1.0) * self.w / 2.0, (1.0 - ndc_y) * self.h / 2.0)

    def end_3d(self):
        """退出 3D 通道：关深度测试，恢复 2D 正交（HUD/特效通道）"""
        glDisable(GL_DEPTH_TEST)
        self._proj_m = None
        self._view_m = None
        if not self.prog3d:
            self._set_ortho_2d()

    def add_tri3d(self, v1, v2, v3, r, g, b, a=255):
        """添加世界坐标三角形（顶点为 (x, y, z) 元组）"""
        self.batch3d.extend([v1[0], v1[1], v1[2], r/255, g/255, b/255, a/255,
                             v2[0], v2[1], v2[2], r/255, g/255, b/255, a/255,
                             v3[0], v3[1], v3[2], r/255, g/255, b/255, a/255])

    def add_quad3d(self, v1, v2, v3, v4, r, g, b, a=255):
        """添加世界坐标四边形（2 个三角形）"""
        self.add_tri3d(v1, v2, v3, r, g, b, a)
        self.add_tri3d(v1, v3, v4, r, g, b, a)

    def flush3d(self):
        """提交 3D 批次：MVP 变换 + 深度测试"""
        batch, self.batch3d = self.batch3d, []
        if not self.ready or not batch:
            return
        n = len(batch) // 7   # 顶点数（stride 7: x,y,z,r,g,b,a）
        verts = (c_float * len(batch))(*batch)
        glBindBuffer(GL_ARRAY_BUFFER, self.vbo3d)
        glBufferData(GL_ARRAY_BUFFER, sizeof(verts), verts, GL_DYNAMIC_DRAW)
        if self.prog3d:
            glUseProgram(self.prog3d)
            glUniformMatrix4fv(self._u_mvp, 1, GL_FALSE, self._mvp)
            # 清掉 2D 通道可能遗留的旧式客户端状态，避免属性串扰
            glDisableClientState(GL_VERTEX_ARRAY)
            glDisableClientState(GL_COLOR_ARRAY)
            glEnableVertexAttribArray(0)
            glEnableVertexAttribArray(1)
            glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 28, c_void_p(0))
            glVertexAttribPointer(1, 4, GL_FLOAT, GL_FALSE, 28, c_void_p(12))
            glDrawArrays(GL_TRIANGLES, 0, n)
            glDisableVertexAttribArray(0)
            glDisableVertexAttribArray(1)
            glUseProgram(0)
        else:
            glEnableClientState(GL_VERTEX_ARRAY)
            glEnableClientState(GL_COLOR_ARRAY)
            glVertexPointer(3, GL_FLOAT, 28, None)
            glColorPointer(4, GL_FLOAT, 28, c_void_p(12))
            glDrawArrays(GL_TRIANGLES, 0, n)
            glDisableClientState(GL_VERTEX_ARRAY)
            glDisableClientState(GL_COLOR_ARRAY)
        glBindBuffer(GL_ARRAY_BUFFER, 0)

    def screenshot(self, path=None):
        """回读当前帧（必须在 flip 之前调用），返回 PIL Image；给 path 则同时保存"""
        from PIL import Image
        data = glReadPixels(0, 0, self.w, self.h, GL_RGB, GL_UNSIGNED_BYTE)
        img = Image.frombytes('RGB', (self.w, self.h), bytes(data))
        img = img.transpose(Image.FLIP_TOP_BOTTOM)   # GL 底行在上，翻回屏幕方向
        if path:
            img.save(path)
        return img

    # ================= 2D 通道（原有行为） =================
    def add_tri(self, x1, y1, x2, y2, x3, y3, r, g, b, a=255):
        """添加一个三角形到批次"""
        self.batch.extend([x1, y1, r/255, g/255, b/255, a/255,
                           x2, y2, r/255, g/255, b/255, a/255,
                           x3, y3, r/255, g/255, b/255, a/255])

    def add_quad(self, x1, y1, x2, y2, x3, y3, x4, y4, r, g, b, a=255):
        """添加四边形（2个三角形）到批次"""
        self.add_tri(x1, y1, x2, y2, x3, y3, r, g, b, a)
        self.add_tri(x1, y1, x3, y3, x4, y4, r, g, b, a)

    def add_polygon(self, pts, r, g, b, a=255):
        """添加多边形（扇形三角剖分）到批次"""
        n = len(pts)
        if n < 3:
            return
        for i in range(1, n - 1):
            self.add_tri(pts[0][0], pts[0][1],
                         pts[i][0], pts[i][1],
                         pts[i+1][0], pts[i+1][1],
                         r, g, b, a)

    def flush(self):
        """提交所有批次到 GPU 绘制"""
        if not self.ready or not self.batch:
            self.batch = []
            return
        n = len(self.batch) // 6  # 顶点数
        verts = (c_float * len(self.batch))(*self.batch)
        glBindBuffer(GL_ARRAY_BUFFER, self.vbo)
        glBufferData(GL_ARRAY_BUFFER, sizeof(verts), verts, GL_DYNAMIC_DRAW)

        glEnableClientState(GL_VERTEX_ARRAY)
        glEnableClientState(GL_COLOR_ARRAY)
        glVertexPointer(2, GL_FLOAT, 24, None)           # offset 0
        glColorPointer(4, GL_FLOAT, 24, c_void_p(8))    # offset 8 (2 floats)
        glDrawArrays(GL_TRIANGLES, 0, n)
        glDisableClientState(GL_VERTEX_ARRAY)
        glDisableClientState(GL_COLOR_ARRAY)

        self.batch = []

    def make_texture(self, name, surface):
        """将 pygame Surface 上传为 OpenGL 纹理"""
        if not self.ready:
            return
        # 删除旧纹理
        if name in self._tex_cache:
            glDeleteTextures(1, [self._tex_cache[name]])
        # 转换 surface 到 RGBA 数据
        w, h = surface.get_size()
        raw = pygame.image.tostring(surface, 'RGBA', True)
        tex = glGenTextures(1)
        try:
            tex = int(tex[0])    # 某些环境返回长度 1 数组
        except (TypeError, IndexError):
            tex = int(tex)       # 标量（含 numpy 0 维标量）
        glBindTexture(GL_TEXTURE_2D, tex)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, raw)
        self._tex_cache[name] = tex

    def draw_texture(self, name, x=0, y=0, alpha=255, w=None, h=None):
        """绘制已缓存的纹理（默认全屏四边形，可指定尺寸）"""
        if name not in self._tex_cache:
            return
        w = self.w if w is None else w
        h = self.h if h is None else h
        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, self._tex_cache[name])
        glColor4f(1, 1, 1, alpha/255)
        glBegin(GL_QUADS)
        glTexCoord2f(0, 0); glVertex2f(x, y)
        glTexCoord2f(1, 0); glVertex2f(x + w, y)
        glTexCoord2f(1, 1); glVertex2f(x + w, y + h)
        glTexCoord2f(0, 1); glVertex2f(x, y + h)
        glEnd()
        glDisable(GL_TEXTURE_2D)

    def draw_hud(self, text, x, y, r=255, g=255, b=255, size=24):
        """用纹理绘制 HUD 文字（P3：替换 glDrawPixels）。

        原实现走 glRasterPos2i + glDrawPixels —— 该路径绕过着色器与顶点数组状态，
        在本管线里会在字形旁拖出横向白条。改为：把字形渲染成 Surface 上传为纹理，
        再用与雾/暗角同一条 draw_texture 通路绘制。

        纹理按「屏幕位置」复用同一槽位名：文字不变则零上传，文字变了原地重传，
        缓存大小恒为 HUD 消息数（不会随速度/毫秒数无限增长）。
        """
        if not self.ready:
            return
        try:
            size = int(size)
            slot = ('hud', x, y, size)
            if self._hud_prev.get(slot) != (text, r, g, b):
                surf = _make_font(size).render(text, True, (r, g, b))
                # draw_texture 的 texcoord 约定会把纹理**垂直镜像**（实测：屏幕左上 ↔
                # Surface 左下）。雾/暗角/射线都是上下对称图，所以这个朝向问题一直没暴露；
                # 文字必须先竖直翻回来。不改 make_texture——那会连带把雾的样子翻掉。
                self.make_texture(slot, pygame.transform.flip(surf, False, True))
                self._hud_prev[slot] = (text, r, g, b)
                self._hud_size[slot] = surf.get_size()
            w, h = self._hud_size.get(slot, (0, 0))
            if w and h:
                self.draw_texture(slot, x, y, 255, w, h)
        except Exception:
            pass

    def close(self):
        """清理 OpenGL 资源"""
        if self.ready:
            glDeleteBuffers(1, [self.vbo])
            glDeleteBuffers(1, [self.vbo3d])
            for name, tex in self._tex_cache.items():
                glDeleteTextures(1, [tex])
            self._tex_cache = {}
            if self.prog3d:
                glDeleteProgram(self.prog3d)
                self.prog3d = 0


# ====================================================================
#  PietRuntime —— 傻执行层，零决策
#  只做一件事：执行 Piet 发出的命令
# ====================================================================
class PietRuntime:
    def __init__(self, piet_image_path, codel_size=1, track_data=None, tree_positions=None, building_data=None):
        # M2: max_steps 调大——首帧 INIT 段约 60-70 万步
        self.vm = PietInterpreter(piet_image_path, codel_size=codel_size,
                                  halt_on_backward=False, max_steps=4_000_000)
        self.screen = None
        self.width = 800
        self.height = 600
        self.clock = pygame.time.Clock()
        self.font = None
        self.font_small = None
        self._font_cache = {}         # P3: EXT_HUD 的 size 参数 → pygame Font 缓存
        self.running = True
        self.quit_flag = 0
        self.frame_count = 0
        # OpenGL 渲染器（PIET_FORCE_2D=1 强制走纯 pygame 2D 回退）
        self.gl = GLRenderer()
        self._use_gl = HAS_GL and not os.environ.get('PIET_FORCE_2D')
        # 性能缓存
        self._halo_cache = {}  # 车灯光晕 Surface 缓存
        # ===== M2: 世界数据全部由 Piet INIT 写入 persist =====
        # track_data/tree_positions/buildings 参数已退役（兼容保留签名），首帧后从 persist 物化
        self.track_data = []
        self.tree_positions = []
        self.buildings = []
        self._world_ready = False
        # 玩家/滚动状态
        self.scroll_z = 0     # 当前前进偏移（持续增加）
        # 视觉参数缓存（每次 _draw_track 刷新）
        self._p3030s = {}
        # 预创建雾效纹理
        self._fog_tex = None
        self._asphalt_tex = None   # 沥青路面纹理
        self._vignette_tex = None  # 暗角纹理
        self._ray_tex = None       # 阳光射线纹理
        # 粒子系统
        self._leaves = []          # 落叶粒子 [x, y, vx, vy, rot, size, life]
        # 渲染性能缓存（物化后重建）
        self._seg_z0 = []
        self._tree_z = []
        self._bld_z = []
        self._sky_cache = None        # 渐变天空 Surface 缓存
        self._sky_cache_key = None
        self._hud_msgs = {}           # P3: HUD 消息模板（物化时从 persist[61000+] 解码）
        self._shadow_cache = {}       # 树影缩放缓存
        # P1 3D 通道状态
        self._backdrop = None         # Piet 发出的最大矩形（草地幕布）颜色
        self._backdrop_area = 0
        self._car_world = None        # 本帧玩家车世界坐标 (x, y, z)
        # P2 车辆 3D 状态
        self._cam3d = None            # 本帧 3D 相机参数（draw_player 复用）

    def _materialize_world(self):
        """M2/M5: INIT 完成后从 persist 纯读数物化世界数据（GL 与 2D 渲染共用）。
        形状数值与生物群系全部来自图片；z 网格/type/side/烟囱属模板推导。"""
        import time
        t0 = time.time()
        p = self.vm.runtime['persist']
        # 赛道：500 段由 501 点曲线表推导（cx1[i] = cx[i+1] 共享）
        n_seg = p.get(51999, 0)
        segs = []
        for i in range(n_seg):
            z0 = i * 200
            segs.append((p.get(50000 + i), p.get(50600 + i), z0,
                         p.get(50000 + i + 1), p.get(50600 + i + 1), z0 + 200,
                         p.get(60000 + i, 0)))
        self.track_data = segs
        # 树：每 3 段两侧，x = cx表[3a] ± 180，palm 由 biome 模板推导
        trees = []
        for a_i in range(167):
            i = a_i * 3
            z = i * 200 + 100
            cx_t = p.get(55000 + i)
            cy_t = p.get(56000 + i)
            palm = -1 if p.get(60000 + i, 0) == 1 else 0
            trees.append((cx_t - 180, cy_t, z, palm))
            trees.append((cx_t + 180, cy_t, z, palm))
        self.tree_positions = trees
        # 建筑：188 组槽（每 2 段一组，两侧共享同组值），persist 值主序布局：
        # 每组 8 键 = d,d,h,h,w,w,u,u（DUPLICATE 双写所致，同 z 两侧同值）；
        # x = cx表[i] ± (320+u)；工厂槽自动附加烟囱（宽10高150 标记）
        blds = []
        zslot = 0
        for i in range(0, 500, 2):
            z = i * 200 + 100
            biome = p.get(60000 + i, 0)
            if biome == 1:
                continue
            cx_t = p.get(55000 + i)
            base = 65000 + zslot * 8
            d = p.get(base)
            h = p.get(base + 2)
            w = p.get(base + 4)
            u = p.get(base + 6)
            for side in (-1, 1):
                x = cx_t + side * (320 + u)
                blds.append((x, z, w, h, d, side, {0: 0, 2: 2, 3: 4}[biome]))
                if biome == 2:
                    blds.append((x + side * 20, z, 10, 150, 10, side, 3))
            zslot += 1
        self.buildings = blds
        # HUD 消息模板（P3）：persist[61000+] 的 UTF-8 字节流 → Python 格式串
        self._hud_msgs = {1: decode_hud_template(p, HUD_KEY0),
                          2: decode_hud_template(p, HUD_KEY1)}
        self._seg_z0 = [s[2] for s in segs]
        self._tree_z = [t[2] for t in trees]
        self._bld_z = [b[1] for b in blds]
        self._world_ready = True
        print(f'[M2] 世界数据物化: 段={len(segs)} 树={len(trees)} 楼={len(blds)}'
              f'  耗时 {time.time()-t0:.2f}s', flush=True)

    def _maybe_capture_frame(self):
        """验证钩子：PIET_SHOT_DIR 设置时，在 flip 前回读指定帧存 PNG（黑屏验证）
        GL 模式用 glReadPixels，2D 模式直接存屏幕 Surface"""
        shot_dir = os.environ.get('PIET_SHOT_DIR')
        if not shot_dir or not self.screen:
            return
        n = self.frame_count
        if n in (2, 10, 15, 20, 33, 40):
            try:
                os.makedirs(shot_dir, exist_ok=True)
                path = os.path.join(shot_dir, f'frame_{n:03d}.png')
                if self._use_gl and self.gl.ready:
                    self.gl.screenshot(path)
                else:
                    pygame.image.save(self.screen, path)
                print(f'[验证] 已回读第 {n} 帧 → {path}', flush=True)
            except Exception as e:
                print(f'[验证] 帧回读失败: {e}', flush=True)

    def _exec_cmd(self, cmd):
        """执行一条 Piet 发出的命令"""
        if cmd[0] == 'create_win':
            w, h = cmd[1], cmd[2]
            self.width = w
            self.height = h
            if self.screen is None:
                if not pygame.get_init():
                    pygame.init()
                # OpenGL 模式优先；任何一步失败自动回退纯 2D 窗口（防黑屏死窗口）
                if self._use_gl:
                    try:
                        pygame.display.gl_set_attribute(pygame.GL_DEPTH_SIZE, 24)
                        pygame.display.gl_set_attribute(pygame.GL_DOUBLEBUFFER, 1)
                        self.screen = pygame.display.set_mode((w, h), pygame.OPENGL | pygame.DOUBLEBUF)
                    except Exception as e:
                        print(f'[GL] 上下文创建失败，回退 2D: {e}', flush=True)
                        self.screen = None
                    if self.screen is not None and not self.gl.init(w, h):
                        print('[GL] 渲染器初始化失败，回退 2D', flush=True)
                        self.screen = None
                    if self.screen is None:
                        self._use_gl = False
                        self.screen = pygame.display.set_mode((w, h))
                else:
                    self.screen = pygame.display.set_mode((w, h))
                pygame.display.set_caption("Piet 3D Engine")
                self.font = _make_font(24)
                self.font_small = _make_font(18)
                self._init_fog_tex(w, h)
                self._init_asphalt_tex()
                self._init_vignette_tex(w, h)
                self._init_ray_tex(w, h)
                # OpenGL 纹理缓存
                if self._use_gl:
                    for name in ['fog', 'vignette', 'ray']:
                        tex = getattr(self, f'_{name}_tex', None)
                        if tex:
                            self.gl.make_texture(name, tex)

        elif cmd[0] == 'close_win':
            self.running = False
            if self._use_gl:
                self.gl.close()

        elif cmd[0] == 'quit_check':
            # 轮询 Pygame 事件，设置退出标志
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.quit_flag = 1
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    self.quit_flag = 1

        elif cmd[0] == 'clear':
            r, g, b = cmd[1], cmd[2], cmd[3]
            self._backdrop = None
            self._backdrop_area = 0
            if self._use_gl and self.gl.ready:
                # GL 模式：清屏 + 渐变天空进批处理（与 2D 版视觉对齐）
                if (r, g, b) == (135, 206, 235):
                    self.gl.clear(r, g, b)
                    self._submit_gradient_sky_gl()
                else:
                    self.gl.clear(r, g, b)
            elif self.screen:
                # 天空色 → 画渐变天空
                if (r, g, b) == (135, 206, 235):
                    self._draw_gradient_sky()
                else:
                    self.screen.fill((r, g, b))

        elif cmd[0] == 'rect':
            # 记录 Piet 发出的最大矩形 = 远景幕布色（3D 地形远处向它渐隐）
            area = cmd[3] * cmd[4]
            if area >= self._backdrop_area:
                self._backdrop_area = area
                self._backdrop = (cmd[5], cmd[6], cmd[7])
            if self._use_gl and self.gl.ready:
                self.gl.add_quad(cmd[1], cmd[2], cmd[1] + cmd[3], cmd[2],
                                 cmd[1] + cmd[3], cmd[2] + cmd[4], cmd[1], cmd[2] + cmd[4],
                                 cmd[5], cmd[6], cmd[7])
            elif self.screen:
                pygame.draw.rect(self.screen, (cmd[5], cmd[6], cmd[7]),
                                 (cmd[1], cmd[2], cmd[3], cmd[4]))

        elif cmd[0] == 'tri':
            if self._use_gl and self.gl.ready:
                self.gl.add_tri(cmd[1], cmd[2], cmd[3], cmd[4], cmd[5], cmd[6],
                                cmd[7], cmd[8], cmd[9])
            elif self.screen:
                pts = [(cmd[1], cmd[2]), (cmd[3], cmd[4]), (cmd[5], cmd[6])]
                pygame.draw.polygon(self.screen, (cmd[7], cmd[8], cmd[9]), pts)

        elif cmd[0] == 'tri_3d':
            # 3D 三角形：参数已经是由 Piet 投影到屏幕坐标的
            if self._use_gl and self.gl.ready:
                self.gl.add_tri(cmd[1], cmd[2], cmd[4], cmd[5], cmd[7], cmd[8],
                                cmd[10], cmd[11], cmd[12])
            elif self.screen:
                pts = [(cmd[1], cmd[2]), (cmd[4], cmd[5]), (cmd[7], cmd[8])]
                pygame.draw.polygon(self.screen, (cmd[10], cmd[11], cmd[12]), pts)

        elif cmd[0] == 'swap':
            if self.screen:
                self._maybe_capture_frame()   # 黑屏验证钩子：必须在 flip 前回读
                # GL 模式：先把残余批次提交到 GPU，再翻转（每帧只翻一次，由 swap 负责）
                if self._use_gl and self.gl.ready:
                    self.gl.flush()
                pygame.display.flip()
            self.frame_count += 1
            # 自动化验证：到帧自动退出（PIET_MAX_FRAMES）
            max_frames = int(os.environ.get('PIET_MAX_FRAMES') or 0)
            if max_frames and self.frame_count >= max_frames:
                self.quit_flag = 1

        elif cmd[0] == 'text':
            if self.screen and self.font:
                surf = self.font.render(cmd[7], True, (cmd[4], cmd[5], cmd[6]))
                self.screen.blit(surf, (cmd[1], cmd[2]))

        # ===== M3: 规则全部由 Piet 决策，Python 只同步/渲染 =====
        elif cmd[0] == 'handle_input':
            # 前向积分已在 Piet 完成（persist[2001]），此处仅同步供渲染投影使用
            self.scroll_z = int(self.vm.runtime['persist'].get(2001, self.scroll_z))

        elif cmd[0] == 'draw_player':
            if self.screen:
                p = self.vm.runtime['persist']
                x = p.get(2000, 400)
                cols = self._read_car_colors(p)
                if self._use_gl and self.gl.ready:
                    if not self._draw_car3d_gl(cols):
                        self._draw_player_gl(x, cols)   # 相机未就绪时退回精灵
                else:
                    self._draw_car(self.screen, x, 455, cols)

        elif cmd[0] == 'draw_track':
            if self.screen:
                if self._use_gl:
                    self._draw_track_gl()
                else:
                    self._draw_track()

        elif cmd[0] == 'hud':
            # P3: HUD 的决策（位置/颜色/哪条消息/参数）全由 Piet 发出；
            #     Python 只把消息模板 + 参数排版成像素（模板存 persist[61000+]，INIT 由 Piet 写入）
            _, hx, hy, hsize, hr, hg, hb, mid, args = cmd
            tmpl = self._hud_msgs.get(mid)
            if tmpl is None or self.screen is None:
                return
            text = tmpl.format(*args)
            if self._use_gl and self.gl.ready:
                self.gl.draw_hud(text, hx, hy, hr, hg, hb, hsize)
            elif self.font:
                surf = self._hud_font(hsize).render(text, True, (hr, hg, hb))
                self.screen.blit(surf, (hx, hy))

    def _hud_font(self, size):
        """HUD 字体缓存：字号由 Piet 的 EXT_HUD size 参数决定（呈现层只负责光栅化）"""
        f = self._font_cache.get(size)
        if f is None:
            f = _make_font(size)
            self._font_cache[size] = f
        return f

    # ======================================================
    # 赛车绘制：2D / GL 共用同一套几何
    # ======================================================
    def _read_car_colors(self, p):
        """从 persist 读取赛车配色（Piet 拥有全部决策）"""
        is_col = p.get(2100, 0)
        body = (p.get(3041, 255), p.get(3042, 60), p.get(3043, 60))
        col = (p.get(3044, 180), p.get(3045, 0), p.get(3046, 120))
        return {
            'body': col if is_col else body,
            'tire': (p.get(3070, 30), p.get(3071, 30), p.get(3072, 30)),
            'win': (p.get(3073, 150), p.get(3074, 200), p.get(3075, 255)),
            'wing': (p.get(3076, 200), p.get(3077, 50), p.get(3078, 50)),
            'ck': (p.get(3047, 255), p.get(3048, 255), p.get(3049, 100)),
        }

    def _draw_car(self, surface, x, cy_base, cols):
        """立体赛车（从后上方视角，多个多边形组成），画到任意 Surface"""
        r, g, b = cols['body']
        tire_r, tire_g, tire_b = cols['tire']
        win_r, win_g, win_b = cols['win']
        wing_r, wing_g, wing_b = cols['wing']
        ck_r, ck_g, ck_b = cols['ck']

        cx = x
        # 1. 轮胎（左右两个，略宽于车身）
        tire_w, tire_h = 8, 22
        pygame.draw.ellipse(surface, (tire_r, tire_g, tire_b),
                            (cx - 20, cy_base + 14, tire_w, tire_h))
        pygame.draw.ellipse(surface, (tire_r, tire_g, tire_b),
                            (cx + 12, cy_base + 14, tire_w, tire_h))

        # 2. 车身主体（梯形：上窄下宽）
        body_top = cy_base - 28
        body_bot = cy_base + 18
        body_w_top = 14
        body_w_bot = 26
        body_pts = [
            (cx - body_w_bot, body_bot),   # 左下
            (cx + body_w_bot, body_bot),   # 右下
            (cx + body_w_top, body_top),   # 右上
            (cx - body_w_top, body_top),   # 左上
        ]
        pygame.draw.polygon(surface, (r, g, b), body_pts)

        # 3. 车头突出（前保险杠）
        nose_pts = [
            (cx - body_w_bot + 6, body_bot + 6),
            (cx + body_w_bot - 6, body_bot + 6),
            (cx + body_w_top, body_top + 6),
            (cx - body_w_top, body_top + 6),
        ]
        nose_c = (min(255, r+30), min(255, g+30), min(255, b+30))
        pygame.draw.polygon(surface, nose_c, nose_pts)

        # 4. 驾驶舱/车窗（梯形，在车身顶部）
        ck_top = body_top + 8
        ck_bot = body_top + 24
        ck_w_top = 6
        ck_w_bot = 12
        ck_pts = [
            (cx - ck_w_bot, ck_bot),
            (cx + ck_w_bot, ck_bot),
            (cx + ck_w_top, ck_top),
            (cx - ck_w_top, ck_top),
        ]
        pygame.draw.polygon(surface, (ck_r, ck_g, ck_b), ck_pts)

        # 5. 后窗（小梯形，在驾驶舱后面）
        rw_pts = [
            (cx - 10, body_bot - 2),
            (cx + 10, body_bot - 2),
            (cx + 8, body_bot - 12),
            (cx - 8, body_bot - 12),
        ]
        pygame.draw.polygon(surface, (win_r, win_g, win_b), rw_pts)

        # 6. 尾翼
        wing_pts = [
            (cx - 18, body_bot - 4),
            (cx + 18, body_bot - 4),
            (cx + 16, body_bot - 12),
            (cx - 16, body_bot - 12),
        ]
        pygame.draw.polygon(surface, (wing_r, wing_g, wing_b), wing_pts)

        # 7. 尾灯（红点）
        pygame.draw.circle(surface, (255, 0, 0), (cx - 16, body_bot - 6), 3)
        pygame.draw.circle(surface, (255, 0, 0), (cx + 16, body_bot - 6), 3)

        # 8. 车灯（前灯，黄白）
        head_r = min(255, int(win_r * 0.8))
        head_g = min(255, int(win_g * 0.9))
        head_b = win_b
        for hx_off in [-14, 14]:
            hx = cx + hx_off
            pygame.draw.circle(surface, (head_r, head_g, head_b),
                               (hx, body_top - 4), 4)
            # 光晕（缓存 Surface，避免每帧新建）
            halo_key = (head_r, head_g, head_b)
            if halo_key not in self._halo_cache:
                halo = pygame.Surface((14, 14), pygame.SRCALPHA)
                pygame.draw.circle(halo, (*halo_key, 60), (7, 7), 7)
                self._halo_cache[halo_key] = halo
            surface.blit(self._halo_cache[halo_key], (hx - 7, body_top - 11))

    def _draw_car3d_gl(self, cols):
        """P2：真 3D 程序化赛车（车身/车舱/尾翼/四轮/车灯），世界坐标 + 深度缓冲。
        姿态 = 路切线偏航 + 横移侧倾 + 前轮转向 + 轮子自转。
        本方法为纯函数式绘制（位姿+配色→几何），AI 车可直接复用（传不同位姿即可）。
        返回 False 表示相机未就绪（调用方退回精灵方案）"""
        if not (self.gl.ready and self._cam3d and self._car_world):
            return False
        cam = self._cam3d
        wx, wy, wz = self._car_world

        # ── P5: 载具姿态全部由 Piet 帧段算出（persist 6200 侧倾 / 6202 转向 / 6204 轮自转）──
        #    原先此处的横移差分滤波（_car_dyn）与 spin = wz/10.0 已删——决策在图片里，
        #    Python 只把 S=CAR_POSE_SCALE 的定点还原成浮点。
        p = self.vm.runtime['persist']
        roll = p.get(CAR_ROLL_KEY, 0) / CAR_POSE_SCALE
        steer = p.get(CAR_STEER_KEY, 0) / CAR_POSE_SCALE
        spin = p.get(CAR_SPIN_KEY, 0) / CAR_POSE_SCALE
        # P4: 路面切线偏航角（persist 69038，毫弧度）——原先此处对赛道曲线取 ±40
        #     差分再作反正切，已删；现与 Piet 的世界曲线同源，且无 floor 量化抖动。
        road_yaw = p.get(ROAD_YAW_KEY, 0) / ROAD_YAW_SCALE

        # ── 姿态变换：局部(+z 前,+y 上) → 侧倾(绕z) → 偏航(绕y) → 平移 ──
        cr_, sr_ = math.cos(roll), math.sin(roll)
        cy_, sy_ = math.cos(road_yaw), math.sin(road_yaw)

        def T(lx, ly, lz):
            xr = lx * cr_ - ly * sr_
            yr = lx * sr_ + ly * cr_
            return (wx + xr * cy_ + lz * sy_, wy + yr, wz - xr * sy_ + lz * cy_)

        gl = self.gl
        body = cols['body']
        tire = cols['tire']
        win = cols['win']
        wing = cols['wing']
        dark = (body[0] * 2 // 3, body[1] * 2 // 3, body[2] * 2 // 3)
        tire_cap = (tire[0] * 2, tire[1] * 2, tire[2] * 2)

        gl.begin_3d(cam_h=cam['cam_h'], fov=cam['fov'], eye=cam['eye'],
                    center=cam['center'], near=2.0, far=9000.0)

        # 贴地阴影
        gl.add_quad3d(T(-36, 0.4, -50), T(36, 0.4, -50), T(36, 0.4, 56), T(-36, 0.4, 56),
                      0, 0, 0, 80)

        # 车身底盘（外扩棱台）+ 座舱（内收棱台，玻璃色）+ 车顶板
        self._frustum3d(gl, T, (-23, -44, 23, 52, 6), (-26, -44, 26, 52, 20), body)
        self._frustum3d(gl, T, (-17, -22, 17, 16, 20), (-12, -16, 12, 6, 34), win,
                        brights=(1.05, 0.8, 0.9, 0.9, 1.0))
        gl.add_quad3d(T(-12, 34, -16), T(12, 34, -16), T(12, 34, 6), T(-12, 34, 6), *dark)

        # 尾翼（翼板 + 支柱）
        self._frustum3d(gl, T, (-4, -46, 4, -40, 20), (-3, -45, 3, -41, 30), dark)
        self._frustum3d(gl, T, (-24, -50, 24, -38, 30), (-24, -50, 24, -38, 34), wing)

        # 车灯（正面大灯 / 尾面红灯，全亮不参与明暗）
        hl = (min(255, win[0] + 105), min(255, win[1] + 55), min(255, win[2] - 0))
        gl.add_quad3d(T(-18, 11, 52.4), T(-8, 11, 52.4), T(-8, 16, 52.4), T(-18, 16, 52.4), *hl)
        gl.add_quad3d(T(8, 11, 52.4), T(18, 11, 52.4), T(18, 16, 52.4), T(8, 16, 52.4), *hl)
        gl.add_quad3d(T(-18, 12, -44.4), T(-8, 12, -44.4), T(-8, 17, -44.4), T(-18, 17, -44.4),
                      255, 40, 40)
        gl.add_quad3d(T(8, 12, -44.4), T(18, 12, -44.4), T(18, 17, -44.4), T(8, 17, -44.4),
                      255, 40, 40)

        # 四轮（前轮带转向角，全部随前进自转）
        for lx, lz, is_front in ((-27, 36, True), (27, 36, True), (-27, -30, False), (27, -30, False)):
            cxw, cyw, czw = T(lx, 10.0, lz)
            yaw_w = road_yaw + (steer if is_front else 0.0)
            self._wheel3d(gl, cxw, cyw, czw, 10.0, 4.0, spin, yaw_w, tire, tire_cap)

        gl.flush3d()
        gl.end_3d()
        return True

    def _draw_player_gl(self, x, cols):
        """GL 模式画赛车：渲染到精灵 Surface → 上传纹理 → 贴图四边形。
        P1：位置由追尾相机投影得到（起伏/弯道上车身会自然跟随移动）"""
        if not self.gl.ready:
            return
        SW_C, SH_C = 80, 104     # 精灵尺寸
        CX_C, CY_C = 40, 60      # 车中心在精灵内的位置
        sprite = pygame.Surface((SW_C, SH_C), pygame.SRCALPHA)
        self._draw_car(sprite, CX_C, CY_C, cols)
        # make_texture 上传时会垂直翻转，此处预先翻回来，保证屏幕上方向正确
        sprite = pygame.transform.flip(sprite, False, True)
        self.gl.make_texture('car', sprite)
        sx, sy = float(x), 455.0
        if self._car_world:
            sp = self.gl.project_point(*self._car_world)
            if sp and -200 < sp[0] < self.width + 200:
                sx = sp[0]
                sy = min(max(sp[1], 240.0), self.height + 80.0)
        self.gl.draw_texture('car', sx - CX_C, sy - 96.0, w=SW_C, h=SH_C)

    def _submit_gradient_sky_gl(self):
        """GL 模式渐变天空：40 条水平带进批处理（视觉对齐 2D 版）"""
        SW, SH = self.width, self.height
        p = self.vm.runtime['persist']
        biome = self._biome_of(self.scroll_z)
        sb = 3330 + biome * 6
        top = (p.get(sb, 50), p.get(sb + 1, 100), p.get(sb + 2, 180))
        bot = (p.get(sb + 3, 135), p.get(sb + 4, 206), p.get(sb + 5, 235))
        band_h = max(1, SH // 40)
        for i in range(40):
            t0 = i / 40
            c = tuple(int(top[k] + (bot[k] - top[k]) * t0) for k in range(3))
            y0 = i * band_h
            y1 = min(SH, (i + 1) * band_h)
            self.gl.add_quad(0, y0, SW, y0, SW, y1, 0, y1, *c)

    # ======================================================
    # PS3 画质：预创建纹理
    # ======================================================
    def _init_asphalt_tex(self):
        """创建沥青路面噪点纹理（64x64 可拼贴）"""
        tex = pygame.Surface((64, 64))
        seed = 42
        for y in range(64):
            for x in range(64):
                # 伪随机噪点，模拟沥青颗粒
                seed = (seed * 16807) % 2147483647
                noise = (seed % 21) - 10  # -10 ~ 10
                v = 128 + noise
                tex.set_at((x, y), (v, v, v))
        self._asphalt_tex = tex

    def _init_vignette_tex(self, w, h):
        """创建暗角纹理：四角渐暗（强度由 persist[3000] 控制）"""
        vig = pygame.Surface((w, h), pygame.SRCALPHA)
        cx, cy = w // 2, h // 2
        max_dist = (cx**2 + cy**2) ** 0.5
        vig_strength = self.vm.runtime['persist'].get(3000, 80)
        for y in range(h):
            for x in range(w):
                dx, dy = x - cx, y - cy
                dist = (dx*dx + dy*dy) ** 0.5
                t = min(1.0, dist / max_dist * 1.3)
                alpha = int(t * t * vig_strength)
                if alpha > 2:
                    vig.set_at((x, y), (0, 0, 0, alpha))
        self._vignette_tex = vig

    def _init_fog_tex(self, w, h):
        """预创建雾化纹理：远景雾 + 高度雾（地面缭绕）
        参数由 persist[3001~3004] 控制
        """
        fog = pygame.Surface((w, h), pygame.SRCALPHA)
        fog_r = self.vm.runtime['persist'].get(3001, 200)
        fog_g = self.vm.runtime['persist'].get(3002, 210)
        fog_b = self.vm.runtime['persist'].get(3003, 220)
        fog_start = self.vm.runtime['persist'].get(3004, 150)

        # 远景雾（从 fog_start 向下逐渐变浓）
        for y in range(fog_start, h, 2):
            alpha = min(160, int((y - fog_start) * 0.6))
            if alpha > 4:
                fog_col = (fog_r, fog_g, fog_b, alpha)
                pygame.draw.line(fog, fog_col, (0, y), (w, y))

        # 高度雾（地面缭绕雾气，在 y≈SH*0.6 附近最浓）
        mist_center = int(h * 0.62)
        mist_width = int(h * 0.25)
        for y in range(mist_center - mist_width, min(h, mist_center + mist_width)):
            t = abs(y - mist_center) / mist_width  # 0~1
            mist_alpha = int(max(0, 1.0 - t * t) * 80)  # 高斯型衰减
            if mist_alpha > 3:
                mist_col = (fog_r, fog_g, fog_b, mist_alpha)
                pygame.draw.line(fog, mist_col, (0, y), (w, y))

        self._fog_tex = fog

    def _init_ray_tex(self, w, h):
        """预创建阳光射线纹理（God Rays）"""
        ray = pygame.Surface((w, h), pygame.SRCALPHA)
        cx_r, cy_r = w // 2, 0
        max_dist = ((w // 2) ** 2 + h ** 2) ** 0.5
        for y in range(0, h, 2):
            for x in range(0, w, 4):
                dx, dy = x - cx_r, y - cy_r
                dist = (dx*dx + dy*dy) ** 0.5
                if dist < 1:
                    continue
                angle = abs(dx) / max(dist, 1)
                dist_f = 1.0 - dist / max_dist
                seed_val = (x * 7 + y * 13 + 42) & 0xFF
                noise_f = 0.7 + (seed_val % 60) / 200
                alpha = int(max(0, (1.0 - angle * 1.5) * dist_f * 60 * noise_f))
                if alpha > 3:
                    ray.set_at((x, y), (255, 240, 200, alpha))
        self._ray_tex = ray

    def _update_particles(self):
        """更新落叶粒子系统"""
        # 每帧衰减存活时间，移除死亡粒子
        self._leaves = [l for l in self._leaves if l[6] > 0]
        # 更新位置
        for leaf in self._leaves:
            leaf[0] += leaf[2]  # x += vx
            leaf[1] += leaf[3]  # y += vy
            leaf[2] *= 0.98     # 水平阻尼
            leaf[3] = min(leaf[3] + 0.05, 1.5)  # 重力加速
            leaf[4] += leaf[5]  # 旋转
            leaf[6] -= 1        # 减少寿命

    def _spawn_leaf(self, sx, sy):
        """在屏幕坐标 (sx, sy) 附近生成一片落叶"""
        leaf = [
            sx + random.randint(-20, 20),
            sy + random.randint(-10, 10),
            random.uniform(-0.3, 0.3),   # vx
            random.uniform(-0.5, 0.0),   # vy
            random.uniform(0, 360),       # rot
            random.uniform(0.5, 1.2),     # rot speed
            random.randint(60, 150),      # life (frames)
        ]
        self._leaves.append(leaf)

    def _draw_particles(self, screen):
        """绘制所有落叶"""
        for leaf in self._leaves:
            x, y, _, _, rot, size_mult, life = leaf
            if x < -50 or x > self.width + 50 or y < -50 or y > self.height + 50:
                continue
            size = max(2, int(4 * size_mult))
            # 落叶颜色（黄褐色，随寿命变暗）
            life_ratio = life / 150.0
            r = int(180 * life_ratio)
            g = int(120 * life_ratio)
            b = int(40 * life_ratio)
            # 用旋转矩形模拟落叶
            leaf_surf = pygame.Surface((size * 2, size), pygame.SRCALPHA)
            pygame.draw.ellipse(leaf_surf, (r, g, b), (0, 0, size * 2, size))
            # 旋转
            rot_surf = pygame.transform.rotate(leaf_surf, rot)
            screen.blit(rot_surf, (x - rot_surf.get_width() // 2,
                                    y - rot_surf.get_height() // 2))

    def _draw_gradient_sky(self):
        """绘制渐变天空：主题色由 persist 控制（Surface 缓存，同色直接 blit）"""
        # 根据当前位置计算当前主题区（M5：查 Piet 写入的生物群系表）
        biome = self._biome_of(self.scroll_z)
        # 每组天空色占用6个地址（顶部RGB+底部RGB），起始3330+biome*6
        sb = 3330 + biome * 6
        p = self.vm.runtime['persist']
        defaults = (50, 100, 180, 135, 206, 235)
        key = tuple(p.get(sb + i, d) for i, d in enumerate(defaults))
        if key != self._sky_cache_key:
            SH = self.height
            sky = pygame.Surface((self.width, SH))
            tr, tg, tb, br, bg, bb = key
            for y in range(SH):
                t = y / SH
                r = int(tr + (br - tr) * t)
                g = int(tg + (bg - tg) * t)
                b = int(tb + (bb - tb) * t)
                pygame.draw.line(sky, (r, g, b), (0, y), (self.width, y))
            self._sky_cache = sky
            self._sky_cache_key = key
        self.screen.blit(self._sky_cache, (0, 0))

    # ======================================================
    # 动态赛道渲染（PS3 级画质）
    # ======================================================
    def _draw_track(self):
        """PS3 级赛道渲染：全部参数由 persist[3011~3040] 控制"""
        SW, SH = self.width, self.height
        CAM_Z0 = -200
        CAM_H = self.vm.runtime['persist'].get(3039, 80)
        FOV = self.vm.runtime['persist'].get(3038, 250)
        ROAD_HALF = self.vm.runtime['persist'].get(3037, 150)
        # 缓存 persist 参数（避免循环内重复查找）
        self._p3030s = self.vm.runtime['persist']
        p = self._p3030s
        sz = self.scroll_z

        # 更新粒子系统
        self._update_particles()

        # ════════════════════════════════════════
        # 第一遍：路面 + 边线 + 中线
        # ════════════════════════════════════════
        seg_start = bisect_left(self._seg_z0, sz - 400)  # z0 < sz-400 的段必然不可见
        for seg_idx in range(seg_start, len(self.track_data)):
            seg = self.track_data[seg_idx]
            cx0, cy0, z0, cx1, cy1, z1, seg_biome = seg
            sz0 = z0 - sz
            sz1 = z1 - sz
            if sz1 < -200:
                continue
            if sz0 > 3000:   # 升序排列，后面全部不可见
                break

            pts = [_project(wx, wy, wz, CAM_Z0, CAM_H, FOV, SW, SH)
                   for wx, wy, wz in [
                       (cx0 - ROAD_HALF, cy0, sz0),
                       (cx0 + ROAD_HALF, cy0, sz0),
                       (cx1 + ROAD_HALF, cy1, sz1),
                       (cx1 - ROAD_HALF, cy1, sz1),
                   ]]

            x1, y1 = pts[0]; x2, y2 = pts[1]; x3, y3 = pts[2]; x4, y4 = pts[3]
            if max(x1,x2,x3,x4) < -50 or min(x1,x2,x3,x4) > SW+50:
                continue
            if max(y1,y2,y3,y4) < -50 or min(y1,y2,y3,y4) > SH+50:
                continue

            avg_z = (sz0 + sz1) // 2
            dist_factor = max(0, 255 - abs(avg_z) // 15)

            # ── PS3+ 沥青材质：双重噪点（粗颗粒+细颗粒）──
            bm_base = 3200 + seg_biome * 10
            r_coef = self._p3030s.get(bm_base, 62)
            g_coef = self._p3030s.get(bm_base + 1, 60 + seg_biome * 10)
            b_coef = self._p3030s.get(bm_base + 2, 68 - seg_biome * 5)
            clamp_min = self._p3030s.get(bm_base + 3, 30)
            clamp_max = self._p3030s.get(bm_base + 4, 255)
            noise_seed = (seg_idx * 12347 + int(avg_z) * 7891) & 0x7FFF
            noise_amp = self._p3030s.get(bm_base + 5, 9)
            noise_coarse = (noise_seed % noise_amp) - (noise_amp // 2)  # 粗颗粒
            noise_fine = ((noise_seed * 137 + 73) % 5) - 2              # 高频细颗粒
            # 铺设方向条纹：沿Z轴方向的微弱纹理
            stripe = int(math.sin(avg_z * 0.05) * 3)
            noise = noise_coarse + noise_fine + stripe
            light_str = self._p3030s.get(3016, 3)
            turn_light = int((cx0 + cx1) * light_str // 100)

            r = _clamp(int(dist_factor * r_coef // 100) + noise + turn_light, clamp_min, clamp_max)
            g = _clamp(int(dist_factor * g_coef // 100) + noise, clamp_min, clamp_max)
            b = _clamp(int(dist_factor * b_coef // 100) + noise - turn_light//2, clamp_min, clamp_max)

            # ── 路面 ──
            pygame.draw.polygon(self.screen, (r, g, b), [pts[0], pts[1], pts[2]])
            pygame.draw.polygon(self.screen, (r, g, b), [pts[0], pts[2], pts[3]])

            # ── 路面反光（潮湿路面高光条纹）──
            ref_strength = self._p3030s.get(3091, 0)
            if ref_strength > 0 and abs(avg_z) < 600:
                ref_seed = (seg_idx * 73 + int(avg_z) * 31) & 0xFF
                if ref_seed > 120:
                    ref_alpha = int(ref_strength * (ref_seed % 60) / 100)
                    offset = (ref_seed % 20) - 10
                    ref_pts = [
                        _project(cx0 + offset, cy0 - 5, sz0, CAM_Z0, CAM_H, FOV, SW, SH),
                        _project(cx0 + offset + 15, cy0 - 5, sz0, CAM_Z0, CAM_H, FOV, SW, SH),
                        _project(cx1 + offset + 15, cy1 - 5, sz1, CAM_Z0, CAM_H, FOV, SW, SH),
                        _project(cx1 + offset, cy1 - 5, sz1, CAM_Z0, CAM_H, FOV, SW, SH),
                    ]
                    # 用小表面绘制高光条纹，避免全屏Surface
                    xs = [int(p[0]) for p in ref_pts]
                    ys = [int(p[1]) for p in ref_pts]
                    min_x, max_x = max(0, min(xs)), min(SW, max(xs))
                    min_y, max_y = max(0, min(ys)), min(SH, max(ys))
                    if max_x > min_x and max_y > min_y:
                        r_light = min(255, r + 60)
                        g_light = min(255, g + 60)
                        b_light = min(255, b + 60)
                        ref_local = pygame.Surface((max_x-min_x, max_y-min_y), pygame.SRCALPHA)
                        local_pts = [(p[0]-min_x, p[1]-min_y) for p in ref_pts]
                        pygame.draw.polygon(ref_local, (r_light, g_light, b_light, ref_alpha), local_pts)
                        self.screen.blit(ref_local, (min_x, min_y))

            # ── 沙滩区：单侧海水 ──
            if seg_biome == 1:
                water_x = cx0 + ROAD_HALF + 150  # 道路右侧水域
                water_pts = [
                    _project(water_x, cy0 - 20, sz0, CAM_Z0, CAM_H, FOV, SW, SH),
                    _project(water_x + 300, cy0 - 10, sz0, CAM_Z0, CAM_H, FOV, SW, SH),
                    _project(water_x + 300, cy1 + 10, sz1, CAM_Z0, CAM_H, FOV, SW, SH),
                    _project(water_x, cy1 + 20, sz1, CAM_Z0, CAM_H, FOV, SW, SH),
                ]
                wx1, wy1 = water_pts[0]; wx2, wy2 = water_pts[1]
                wx3, wy3 = water_pts[2]; wx4, wy4 = water_pts[3]
                if not (max(wx1,wx2,wx3,wx4) < -50 or min(wx1,wx2,wx3,wx4) > SW+50):
                    w_bright = max(0.2, 1.0 - abs(avg_z) / 2000)
                    w_r = int(30 * w_bright); w_g = int(100 * w_bright); w_b = int(200 * w_bright)
                    pygame.draw.polygon(self.screen, (w_r, w_g, w_b), water_pts)
                    # 动态波光（多点闪烁，随时间变化）
                    if abs(avg_z) < 600:
                        for wi in range(6):
                            w_seed = (seg_idx * 31 + wi * 137 + int(self.scroll_z // 30)) & 0xFF
                            if w_seed > 140:
                                spark_x = wx1 + (wx4 - wx1) * (wi / 6)
                                spark_y = wy1 + (wy4 - wy1) * (wi / 6)
                                sp_alpha = min(200, 80 + (w_seed % 120))
                                sp_size = max(2, 4 - abs(avg_z) // 200)
                                pygame.draw.circle(self.screen,
                                    (240, 250, 255, sp_alpha),
                                    (int(spark_x), int(spark_y)), sp_size)

            # ── 边线（红白交替，主题色由 persist 控制）──
            edge_w = self._p3030s.get(3040, 8)
            e_base = 3240 + seg_biome * 6  # 每主题6个参数
            e_red_r = self._p3030s.get(e_base, 255)
            e_red_g = self._p3030s.get(e_base + 1, 50)
            e_red_b = self._p3030s.get(e_base + 2, 50)
            e_wht_r = self._p3030s.get(e_base + 3, 255)
            e_wht_g = self._p3030s.get(e_base + 4, 255)
            e_wht_b = self._p3030s.get(e_base + 5, 255)
            edge_col = (e_red_r, e_red_g, e_red_b) if (seg_idx // 3) % 2 == 0 else (e_wht_r, e_wht_g, e_wht_b)
            l_pts = [_project(cx0 - ROAD_HALF, cy0, sz0, CAM_Z0, CAM_H, FOV, SW, SH),
                     _project(cx0 - ROAD_HALF + edge_w, cy0, sz0, CAM_Z0, CAM_H, FOV, SW, SH),
                     _project(cx1 - ROAD_HALF + edge_w, cy1, sz1, CAM_Z0, CAM_H, FOV, SW, SH),
                     _project(cx1 - ROAD_HALF, cy1, sz1, CAM_Z0, CAM_H, FOV, SW, SH)]
            pygame.draw.polygon(self.screen, edge_col, l_pts)
            r_pts = [_project(cx0 + ROAD_HALF - edge_w, cy0, sz0, CAM_Z0, CAM_H, FOV, SW, SH),
                     _project(cx0 + ROAD_HALF, cy0, sz0, CAM_Z0, CAM_H, FOV, SW, SH),
                     _project(cx1 + ROAD_HALF, cy1, sz1, CAM_Z0, CAM_H, FOV, SW, SH),
                     _project(cx1 + ROAD_HALF - edge_w, cy1, sz1, CAM_Z0, CAM_H, FOV, SW, SH)]
            pygame.draw.polygon(self.screen, edge_col, r_pts)

            # ── 中线虚线（亮度基准由 persist[3024] 控制）──
            if avg_z > -50:
                m_base = self._p3030s.get(3024, 80)
                for offset in [-30, 0, 30]:
                    t = 0.3 + (offset + 30) / 100
                    mz = sz0 + (sz1 - sz0) * t
                    mx = cx0 + (cx1 - cx0) * t
                    my = cy0 + (cy1 - cy0) * t + 3
                    ml = _project(mx - 6, my, mz, CAM_Z0, CAM_H, FOV, SW, SH)
                    mr = _project(mx + 6, my, mz, CAM_Z0, CAM_H, FOV, SW, SH)
                    m_bright = max(m_base, 255 - abs(int(mz)) // 20)
                    pygame.draw.line(self.screen, (m_bright, m_bright, m_bright),
                                     ml, mr, max(1, 4 - abs(int(mz)) // 500))

        # ════════════════════════════════════════
        # 第二遍：树木阴影投射到路面
        # ════════════════════════════════════════
        shadow_tex = getattr(self, '_shadow_tex', None)
        if shadow_tex is None:
            # 预创建阴影纹理（径向渐变羽化边缘）
            sh_alpha = self._p3030s.get(3034, 70)
            shadow_tex = pygame.Surface((80, 24), pygame.SRCALPHA)
            cx_sh, cy_sh = 40, 12
            max_r = (cx_sh**2 + cy_sh**2) ** 0.5
            for sy_sh in range(24):
                for sx_sh in range(80):
                    dx = sx_sh - cx_sh
                    dy = sy_sh - cy_sh
                    dist_ratio = (dx*dx + dy*dy) ** 0.5 / max_r
                    # 中心不衰减，边缘平滑过渡到0
                    alpha_factor = max(0.0, 1.0 - dist_ratio * dist_ratio)
                    a = int(sh_alpha * alpha_factor)
                    if a > 2:
                        shadow_tex.set_at((sx_sh, sy_sh), (0, 0, 0, a))
            self._shadow_tex = shadow_tex

        for item in self.tree_positions[bisect_left(self._tree_z, sz - 200):]:
            wx, wy, wz = item[0], item[1], item[2]
            twz = wz - sz
            if twz > 3000:
                break
            # 阴影在地面高度投影
            sx, sy = _project(wx, 0, twz, CAM_Z0, CAM_H, FOV, SW, SH)
            if sy < 200 or sy > 650:
                continue
            # 阴影大小随距离变化
            s_scale = max(0.15, 1.0 - abs(twz) / 2500)
            sw = int(80 * s_scale)
            sh = int(24 * s_scale)
            if sw < 4 or sh < 2:
                continue
            scaled_shadow = self._shadow_cache.get((sw, sh))
            if scaled_shadow is None:
                scaled_shadow = pygame.transform.scale(shadow_tex, (sw, sh))
                if len(self._shadow_cache) > 512:
                    self._shadow_cache.clear()
                self._shadow_cache[(sw, sh)] = scaled_shadow
            self.screen.blit(scaled_shadow, (int(sx - sw//2), int(sy - sh//2)))

        # ════════════════════════════════════════
        # 第三遍：绘制树木（普通树+棕榈树）
        # ════════════════════════════════════════
        for item in self.tree_positions[bisect_left(self._tree_z, sz - 200):]:
            if len(item) >= 4:
                wx, wy, wz, tree_type = item
            else:
                wx, wy, wz = item; tree_type = 0
            twz = wz - sz
            if twz > 3000:   # 升序排列，后面全部不可见
                break

            bx, by = _project(wx, wy, twz, CAM_Z0, CAM_H, FOV, SW, SH)
            tx, ty = _project(wx, wy+50, twz, CAM_Z0, CAM_H, FOV, SW, SH)

            if tree_type == -1:  # ── 沙滩棕榈树 ──
                # 棕榈树干（弯向道路）
                trunk_pts = [
                    _project(wx, wy, twz, CAM_Z0, CAM_H, FOV, SW, SH),
                    _project(wx + 15, wy + 60, twz, CAM_Z0, CAM_H, FOV, SW, SH),
                    _project(wx + 5, wy + 70, twz, CAM_Z0, CAM_H, FOV, SW, SH),
                    _project(wx - 5, wy + 10, twz, CAM_Z0, CAM_H, FOV, SW, SH),
                ]
                trunk_bright = max(0.4, 1.0 - abs(twz) / 2500)
                tr_c = (int(140*trunk_bright), int(100*trunk_bright), int(50*trunk_bright))
                pygame.draw.polygon(self.screen, tr_c, trunk_pts)
                # 棕榈叶（扇形散开）
                leaf_bright = max(0.3, 1.0 - abs(twz) / 2000)
                leaf_c = (int(50*leaf_bright), int(160*leaf_bright), int(50*leaf_bright))
                for leaf_angle in range(-60, 70, 30):
                    lx = wx + int(math.sin(math.radians(leaf_angle)) * 50)
                    ly = wy + 80 + int(math.cos(math.radians(leaf_angle)) * 10)
                    leaf_top = _project(lx, ly, twz, CAM_Z0, CAM_H, FOV, SW, SH)
                    leaf_base = _project(wx + 5, wy + 70, twz, CAM_Z0, CAM_H, FOV, SW, SH)
                    if abs(leaf_top[0]) < SW+100:
                        pygame.draw.line(self.screen, leaf_c, leaf_base, leaf_top,
                            max(1, 4 - abs(twz)//400))
            else:  # ── 普通树木（三角形树冠） ──
                # 树冠层次（颜色由 persist[3025~3033] 控制）
                lc_r = self._p3030s.get(3025, 34)
                lc_g = self._p3030s.get(3026, 139)
                lc_b = self._p3030s.get(3027, 34)
                uc_r = self._p3030s.get(3028, 60)
                uc_g = self._p3030s.get(3029, 180)
                uc_b = self._p3030s.get(3030, 60)
                crown_data = [
                    (wx, wy+70, 40, 50, lc_r, lc_g, lc_b),   # 下层：深绿，较宽
                    (wx, wy+100, 30, 40, uc_r, uc_g, uc_b),  # 上层：亮绿，较窄
                ]
                for cwx, cwy, cw, ch, cr, cg, cb in crown_data:
                    c_pts = [
                        _project(cwx, cwy, twz, CAM_Z0, CAM_H, FOV, SW, SH),
                        _project(cwx - cw, cwy + ch, twz, CAM_Z0, CAM_H, FOV, SW, SH),
                        _project(cwx + cw, cwy + ch, twz, CAM_Z0, CAM_H, FOV, SW, SH),
                    ]
                    all_cx = [p[0] for p in c_pts]
                    all_cy = [p[1] for p in c_pts]
                    if (max(all_cx) < -50 or min(all_cx) > SW+50 or
                        max(all_cy) < -50 or min(all_cy) > SH+50):
                        continue
                    c_pts = [(_clamp(p[0], 1, SW-1), _clamp(p[1], 1, SH-1)) for p in c_pts]
                    # 根据距离调暗树冠
                    tree_bright = max(0.4, 1.0 - abs(twz) / 3000)
                    pygame.draw.polygon(self.screen,
                        (int(cr*tree_bright), int(cg*tree_bright), int(cb*tree_bright)),
                        c_pts)

                # 树干
                if abs(bx) < SW+50 and abs(by) < SH+50:
                    tw = max(2, abs(tx-bx)//5)
                    trunk_h = abs(int(ty - by))
                    if trunk_h > 1:
                        trunk_bright = max(0.4, 1.0 - abs(twz) / 3000)
                        tr_r = self._p3030s.get(3031, 101)
                        tr_g = self._p3030s.get(3032, 67)
                        tr_b = self._p3030s.get(3033, 33)
                        tc = (int(tr_r*trunk_bright), int(tr_g*trunk_bright), int(tr_b*trunk_bright))
                        pygame.draw.rect(self.screen, tc,
                            (_clamp(bx-tw, 1, SW-1), _clamp(min(by, ty), 1, SH-1),
                             tw*2, trunk_h))

                # 落叶生成：可见树木附近概率飘落
                if abs(twz) < 800 and random.random() < 0.02:
                    self._spawn_leaf(bx, ty)

        # ════════════════════════════════════════
        # 第四遍：建筑（城市/工厂/烟囱/公园亭子）
        # ════════════════════════════════════════
        b_enabled = self._p3030s.get(3050, 1)
        if b_enabled and self.buildings:
            b_col_r = self._p3030s.get(3051, 180)
            b_col_g = self._p3030s.get(3052, 190)
            b_col_b = self._p3030s.get(3053, 210)
            b_roof_r = self._p3030s.get(3054, 100)
            b_roof_g = self._p3030s.get(3055, 100)
            b_roof_b = self._p3030s.get(3056, 120)
            b_win_r = self._p3030s.get(3057, 255)
            b_win_g = self._p3030s.get(3058, 255)
            b_win_b = self._p3030s.get(3059, 200)
            b_roof_h = self._p3030s.get(3060, 8)
            b_win_s = self._p3030s.get(3061, 10)
            b_win_w = self._p3030s.get(3062, 6)
            # 工厂区颜色偏移
            fct_r = self._p3030s.get(3264, 100)
            fct_g = self._p3030s.get(3264 + 1, 90)
            fct_b = self._p3030s.get(3264 + 2, 80)
            f_roof_r = self._p3030s.get(3264 + 3, 80)
            f_roof_g = self._p3030s.get(3264 + 4, 70)
            f_roof_b = self._p3030s.get(3264 + 5, 60)
            # 公园区颜色偏移
            pk_r = self._p3030s.get(3270, 220)
            pk_g = self._p3030s.get(3270 + 1, 200)
            pk_b = self._p3030s.get(3270 + 2, 160)
            for bld in self.buildings[bisect_left(self._bld_z, sz - 200):]:
                bx3d = bld[0]; bz3d = bld[1]; bw = bld[2]
                bh = bld[3]; bd = bld[4]; b_side = bld[5]
                twz = bz3d - sz
                if twz > 3000:
                    break

                b_bright = max(0.35, 1.0 - abs(twz) / 2500)

                # 提取建筑类型（新格式：7个元素才有类型标签）
                if len(bld) >= 7:
                    b_type = bld[6]
                else:
                    b_type = 0

                # 正面裁剪
                face_pts = [
                    _project(bx3d - bw//2, 0,   twz, CAM_Z0, CAM_H, FOV, SW, SH),
                    _project(bx3d + bw//2, 0,   twz, CAM_Z0, CAM_H, FOV, SW, SH),
                    _project(bx3d + bw//2, bh,  twz, CAM_Z0, CAM_H, FOV, SW, SH),
                    _project(bx3d - bw//2, bh,  twz, CAM_Z0, CAM_H, FOV, SW, SH),
                ]
                fx = [p[0] for p in face_pts]
                fy = [p[1] for p in face_pts]
                if (max(fx) < -50 or min(fx) > SW+50 or
                    max(fy) < -50 or min(fy) > SH+50):
                    continue

                if b_type == 3:  # ── 烟囱（工厂区烟雾粒子） ──
                    ch_c = (int(80*b_bright), int(75*b_bright), int(70*b_bright))
                    pygame.draw.polygon(self.screen, ch_c, [
                        face_pts[0], face_pts[1], face_pts[2], face_pts[3]
                    ])
                    # 烟囱顶红砖边
                    pygame.draw.rect(self.screen, (120, 40, 40),
                        (face_pts[0][0], face_pts[2][1] - 4,
                         face_pts[1][0] - face_pts[0][0], 4))
                    # 烟雾粒子动画
                    if abs(twz) < 800:
                        smoke_key = (int(bx3d), int(bz3d))
                        if not hasattr(self, '_smoke_ptcl'):
                            self._smoke_ptcl = {}
                        if smoke_key not in self._smoke_ptcl:
                            self._smoke_ptcl[smoke_key] = [
                                [random.randint(0, 30), random.randint(-20, 0)] for _ in range(6)
                            ]
                        for pi in range(len(self._smoke_ptcl[smoke_key])):
                            p = self._smoke_ptcl[smoke_key][pi]
                            p[1] -= 2; p[0] += random.randint(-1, 1)
                            if p[1] < -60: p[1] = 0; p[0] = random.randint(0, 30)
                            sp = _project(bx3d - bw//2 + p[0], 0, bz3d + p[1] - sz,
                                          CAM_Z0, CAM_H, FOV, SW, SH)
                            if 0 < sp[0] < SW and 0 < sp[1] < SH:
                                sa = max(30, 80 + p[1])
                                ss = max(3, 10 + p[1]//2)
                                sf = pygame.Surface((ss*2, ss*2), pygame.SRCALPHA)
                                pygame.draw.circle(sf, (150, 150, 150, sa), (ss, ss), ss)
                                self.screen.blit(sf, (sp[0]-ss, sp[1]-ss))

                elif b_type == 4:  # ── 公园亭子 ──
                    # 木色柱子 + 尖顶
                    pk_c = (int(pk_r*b_bright), int(pk_g*b_bright), int(pk_b*b_bright))
                    pygame.draw.polygon(self.screen, pk_c, [
                        face_pts[0], face_pts[1], face_pts[2], face_pts[3]
                    ])
                    peak = _project(bx3d, bh + 15, twz, CAM_Z0, CAM_H, FOV, SW, SH)
                    pygame.draw.polygon(self.screen, (180, 60, 60), [
                        face_pts[2], face_pts[3], peak
                    ])

                elif b_type == 2:  # ── 工厂建筑 ──
                    fc = (int(fct_r*b_bright), int(fct_g*b_bright), int(fct_b*b_bright))
                    pygame.draw.polygon(self.screen, fc, [
                        face_pts[0], face_pts[1], face_pts[2], face_pts[3]
                    ])
                    # 平顶 + 侧面管道
                    roof_c = (int(f_roof_r*b_bright), int(f_roof_g*b_bright), int(f_roof_b*b_bright))
                    sd = bd if b_side > 0 else -bd
                    pygame.draw.polygon(self.screen, roof_c, [
                        face_pts[1], face_pts[2],
                        _project(bx3d + sd, bh, twz, CAM_Z0, CAM_H, FOV, SW, SH),
                        _project(bx3d + sd, 0, twz, CAM_Z0, CAM_H, FOV, SW, SH),
                    ])
                    # 大圆管
                    px = _project(bx3d + sd//2, bh + 20, twz, CAM_Z0, CAM_H, FOV, SW, SH)
                    pygame.draw.circle(self.screen, (120, 110, 100), (int(px[0]), int(px[1])), 6)

                else:  # ── 普通建筑（城市/默认） ──
                    fc = (int(b_col_r*b_bright), int(b_col_g*b_bright), int(b_col_b*b_bright))
                    pygame.draw.polygon(self.screen, fc, [
                        face_pts[0], face_pts[1], face_pts[2], face_pts[3]
                    ])
                    # 三角形屋顶
                    roof_top = _project(bx3d, bh + b_roof_h, twz, CAM_Z0, CAM_H, FOV, SW, SH)
                    rc = (int(b_roof_r*b_bright), int(b_roof_g*b_bright), int(b_roof_b*b_bright))
                    pygame.draw.polygon(self.screen, rc, [face_pts[2], face_pts[3], roof_top])
                    # 侧面
                    side_off = bd if b_side > 0 else -bd
                    side_pts = [
                        _project(bx3d + side_off, 0,  twz, CAM_Z0, CAM_H, FOV, SW, SH),
                        _project(bx3d + side_off, bh, twz, CAM_Z0, CAM_H, FOV, SW, SH),
                        face_pts[2], face_pts[1],
                    ]
                    sc = (int(b_col_r*b_bright*0.7), int(b_col_g*b_bright*0.7), int(b_col_b*b_bright*0.7))
                    pygame.draw.polygon(self.screen, sc, side_pts)
                    # 窗户阵列
                    win_c = (int(b_win_r*b_bright), int(b_win_g*b_bright), int(b_win_b*b_bright))
                    win_start_y = b_win_s
                    win_end_y = bh - b_win_s
                    win_start_x = -bw//2 + b_win_s
                    win_end_x = bw//2 - b_win_s
                    win_w = min(b_win_w, (bw - 2*b_win_s) // 4)
                    for wy in range(win_start_y, int(win_end_y), int(b_win_s * 2)):
                        for wx3d in range(win_start_x, int(win_end_x), int(b_win_s * 3)):
                            w_pts = [
                                _project(bx3d + wx3d, wy, twz, CAM_Z0, CAM_H, FOV, SW, SH),
                                _project(bx3d + wx3d + win_w, wy, twz, CAM_Z0, CAM_H, FOV, SW, SH),
                                _project(bx3d + wx3d + win_w, wy + win_w, twz, CAM_Z0, CAM_H, FOV, SW, SH),
                                _project(bx3d + wx3d, wy + win_w, twz, CAM_Z0, CAM_H, FOV, SW, SH),
                            ]
                            win_seed = (int(bx3d * 100 + wy * 7 + wx3d * 13)) & 0xFF
                            if win_seed > 80:
                                wb = 0.6 + (win_seed % 40) / 100
                                wc = (min(255, int(win_c[0]*wb)),
                                      min(255, int(win_c[1]*wb)),
                                      min(255, int(win_c[2]*wb)))
                                pygame.draw.polygon(self.screen, wc, w_pts)

        # ════════════════════════════════════════
        # 后期处理：雾化 + 暗角 + 粒子 + 阳光射线
        # ════════════════════════════════════════
        if self._fog_tex:
            self.screen.blit(self._fog_tex, (0, 0))
        if self._vignette_tex:
            self.screen.blit(self._vignette_tex, (0, 0))
        # 绘制粒子（落叶）
        self._draw_particles(self.screen)
        # 阳光射线（God Rays）
        ray_str = self._p3030s.get(3090, 60)
        if self._ray_tex and ray_str > 0:
            self._ray_tex.set_alpha(ray_str)
            self.screen.blit(self._ray_tex, (0, 0))

        # ═══ P3: HUD 仪表（速度表/转速盘/小地图）——数值全来自 persist ═══
        emit_hud_gauges(_PyPoly(self.screen), p, self._cx_at, sz, SW, SH)

        # HUD 已由 Piet 的 EXT_HUD 命令绘制（P3），此处不再硬编码

    # ======================================================
    # OpenGL 批渲染（完整替代 pygame.draw 版）
    # ======================================================
    # ---- P1：赛道几何辅助（与 build_game_core 的生成公式严格一致）----
    BIOME_KEY0 = 60000   # 生物群系表基址（M5：Piet INIT 写入 501 项）

    def _biome_of(self, z):
        """生物群系索引：查 Piet 写入的 persist 表（Python 不再算 (z//25000)&3）。
        段长 200 → 索引 z//200，钳到 [0,500]（含 100000 回绕端点）。"""
        i = z // 200
        if i < 0:
            i = 0
        elif i > 500:
            i = 500
        p = self._p3030s or self.vm.runtime['persist']
        return p.get(self.BIOME_KEY0 + i, 0)

    CX_KEY0 = 50000    # 赛道 cx 表基址（M2：Piet INIT 写入 501 项，z = 200i）
                       # ↑ 同时是 _road_cx 的表源（C1 统一后不再有第二份 float 实现）

    def _cx_at(self, z):
        """赛道中心线 x（小地图用）：查 Piet INIT 写入的表，不在 Python 里重算曲线。
        与 _road_cx 同源（两者都查同一张表）。"""
        i = z // 200
        if i < 0:
            i = 0
        elif i > 500:
            i = 500
        p = self._p3030s or self.vm.runtime['persist']
        return p.get(self.CX_KEY0 + i, 0)

    CY_KEY0 = 50600    # 赛道 cy 表基址（与 cx 同构，z = 200i）

    def _curve_at(self, key0, z):
        """赛道曲线查表 + 线性插值（C1：消除 float 重算的第二份真源）。

        原先 `_road_cx/_road_cy` 用 math.sin 重算曲线，与图片里的表是两份真源；
        而路面几何是按表点**连线**（分段线性四边形）画的。故此处也按表点插值：
        两边严格同源，且与 float sin 的偏差 <1 世界单位（实测插值误差 ≈0.87）。
        """
        p = self._p3030s or self.vm.runtime['persist']
        i = z // 200
        if i < 0:
            i = 0
        elif i > 499:
            i = 499                      # 表有 501 项，i+1 仍安全
        a = p.get(key0 + i, 0)
        b = p.get(key0 + i + 1, a)
        return a + (b - a) * ((z - i * 200) / 200.0)

    def _road_cx(self, z):
        return self._curve_at(self.CX_KEY0, z)

    def _road_cy(self, z):
        return self._curve_at(self.CY_KEY0, z)

    def _terrain_y(self, x, z, cy):
        """地形高度：路缘(162)内与路面齐平，向外滚出丘陵。
        沙滩区右侧例外：沙坡下探入海（与海面 y=cy-14 衔接）。
        确定性函数（只依赖 x,z），跨段无裂缝。"""
        d = abs(x - self._road_cx(z))
        if d <= 162:
            return cy
        if self._biome_of(z) == 1 and x > self._road_cx(z):
            return cy - 14.0 * min(1.0, (d - 162) / 138.0)
        rise = d - 162
        h = min(rise * 0.3, 55) * (1.0 + 0.45 * math.sin(d * 0.011 + z * 0.0016))
        if d > 900:
            h += min((d - 900) * 0.18, 90) * (1.0 + 0.4 * math.sin(d * 0.005 - z * 0.0009))
        return cy + h

    def _box3d(self, gl, cx, cz, sx, sz_, y0, y1, col, top=False, top_col=None, side_dim=0.72):
        """轴对齐盒体：四面侧壁（±x 面按 side_dim 调暗 = 伪光照），可选顶盖"""
        hx, hz = sx / 2.0, sz_ / 2.0
        x0, x1 = cx - hx, cx + hx
        z0b, z1b = cz - hz, cz + hz
        r, g, b = col
        rd, gd, bd = int(r * side_dim), int(g * side_dim), int(b * side_dim)
        gl.add_quad3d((x0, y0, z0b), (x1, y0, z0b), (x1, y1, z0b), (x0, y1, z0b), r, g, b)
        gl.add_quad3d((x1, y0, z1b), (x0, y0, z1b), (x0, y1, z1b), (x1, y1, z1b), r, g, b)
        gl.add_quad3d((x0, y0, z1b), (x0, y0, z0b), (x0, y1, z0b), (x0, y1, z1b), rd, gd, bd)
        gl.add_quad3d((x1, y0, z0b), (x1, y0, z1b), (x1, y1, z1b), (x1, y1, z0b), rd, gd, bd)
        if top:
            tc = top_col or col
            gl.add_quad3d((x0, y1, z0b), (x1, y1, z0b), (x1, y1, z1b), (x0, y1, z1b), *tc)

    def _pyramid3d(self, gl, cx, cz, r, y0, y1, col):
        """四棱锥（树冠/亭顶）：底面正方形边长 2r，顶点 (cx, y1, cz)"""
        x0, x1 = cx - r, cx + r
        z0b, z1b = cz - r, cz + r
        r_, g_, b_ = col
        gl.add_tri3d((x0, y0, z0b), (x1, y0, z0b), (cx, y1, cz), r_, g_, b_)
        gl.add_tri3d((x1, y0, z1b), (x0, y0, z1b), (cx, y1, cz), r_, g_, b_)
        gl.add_tri3d((x0, y0, z1b), (x0, y0, z0b), (cx, y1, cz), int(r_*0.8), int(g_*0.8), int(b_*0.8))
        gl.add_tri3d((x1, y0, z0b), (x1, y0, z1b), (cx, y1, cz), int(r_*0.8), int(g_*0.8), int(b_*0.8))

    def _frustum3d(self, gl, T, b, t, col, brights=(0.9, 0.7, 0.8, 0.8, 1.1), top_col=None):
        """棱台体（P2 车体件）：底面矩形 b=(bx0,bz0,bx1,bz1,yb) → 顶面矩形 t。
        坐标为车局部系（+z 前，+y 上），经姿态变换 T 后提交。
        brights：前(+z)/后(-z)/左(-x)/右(+x)/顶 五面伪光照系数"""
        bx0, bz0, bx1, bz1, yb = b
        tx0, tz0, tx1, tz1, yt = t
        r, g, b_ = col

        def q(p1, p2, p3, p4, br):
            gl.add_quad3d(T(*p1), T(*p2), T(*p3), T(*p4),
                          min(255, int(r * br)), min(255, int(g * br)), min(255, int(b_ * br)))
        q((bx0, yb, bz1), (bx1, yb, bz1), (tx1, yt, tz1), (tx0, yt, tz1), brights[0])
        q((bx1, yb, bz0), (bx0, yb, bz0), (tx0, yt, tz0), (tx1, yt, tz0), brights[1])
        q((bx0, yb, bz0), (bx0, yb, bz1), (tx0, yt, tz1), (tx0, yt, tz0), brights[2])
        q((bx1, yb, bz1), (bx1, yb, bz0), (tx1, yt, tz0), (tx1, yt, tz1), brights[3])
        tc = top_col or col
        gl.add_quad3d(T(tx0, yt, tz0), T(tx1, yt, tz0), T(tx1, yt, tz1), T(tx0, yt, tz1),
                      min(255, int(tc[0]*brights[4])), min(255, int(tc[1]*brights[4])),
                      min(255, int(tc[2]*brights[4])))

    def _wheel3d(self, gl, cx, cy, cz, r, half_w, spin, yaw, col, cap_col):
        """八棱柱车轮（P2）：spin 绕轮轴自转（由前进距离驱动），yaw 绕竖轴转向。
        轮面保持竖直（车身侧倾时轮子贴地不随倾——等价悬挂行程）"""
        cph, sph = math.cos(yaw), math.sin(yaw)
        ring = []
        for k in range(8):
            a = spin + k * math.pi / 4.0
            ring.append((math.sin(a) * r, math.cos(a) * r))   # (dz, dy) 轮平面内
        pts = []
        for side in (-1, 1):
            for dz, dy in ring:
                pts.append((cx + side * half_w * cph + dz * sph,
                            cy + dy,
                            cz - side * half_w * sph + dz * cph))
        for k in range(8):   # 胎面 8 面
            k2 = (k + 1) % 8
            gl.add_quad3d(pts[k], pts[k2], pts[8 + k2], pts[8 + k], *col)
        for base in (0, 8):  # 两端轮毂盖（扇形）
            for k in range(1, 7):
                gl.add_tri3d(pts[base], pts[base + k], pts[base + k + 1], *cap_col)

    def _draw_track_gl(self):
        """OpenGL 3D 通道：真透视相机 + 深度缓冲
        地形/路面/路肩/护栏/海面/树木/建筑全部世界坐标网格"""
        SW, SH = self.width, self.height
        p = self.vm.runtime['persist']
        CAM_H = p.get(3039, 80)
        FOV = p.get(3038, 250)
        ROAD_HALF = p.get(3037, 150)
        self._p3030s = p
        sz = self.scroll_z
        gl = self.gl

        # 追尾相机姿态 + 车世界坐标：全部由 Piet 帧段算出（M4；persist 69010/69020~69032）
        cam_x = p.get(69020, 0)
        cam_y = p.get(69022, CAM_H)
        cam_z = p.get(69024, sz - 220)
        look_x = p.get(69026, 0)
        look_y = p.get(69028, cam_y - 12)
        self._car_world = (p.get(69010, 0), p.get(69032, 0), float(sz))

        # 验证钩子：PIET_CAM_ORBIT=角度 → 环绕玩家车的检视相机（验证"可旋转视角见全貌"）
        orbit = os.environ.get('PIET_CAM_ORBIT')
        look_z = p.get(69030, sz + 400)
        if orbit:
            try:
                a_orb = math.radians(float(orbit))
                cx_, cy_, cz_ = self._car_world
                cam_x = cx_ + math.sin(a_orb) * 240.0
                cam_y = cy_ + 55.0
                cam_z = cz_ + math.cos(a_orb) * 240.0
                look_x, look_y, look_z = cx_, cy_ + 12.0, cz_
            except ValueError:
                pass

        # 记录相机参数（draw_player 的 3D 车辆通道复用）
        self._cam3d = {'eye': (cam_x, cam_y, cam_z), 'center': (look_x, look_y, look_z),
                       'fov': FOV, 'cam_h': CAM_H}

        # 更新粒子（与 pygame 版共享）
        self._update_particles()

        # 先提交 2D 底层：天空渐变 + Piet 草地矩形（远景幕布）
        gl.flush()

        gl.begin_3d(cam_h=CAM_H, fov=FOV, eye=(cam_x, cam_y, cam_z),
                    center=(look_x, look_y, look_z), near=2.0, far=9000.0)

        backdrop = self._backdrop or (34, 139, 34)
        bd_terrain = {0: (60, 125, 55), 1: (194, 178, 128),
                      2: (95, 100, 88), 3: (45, 150, 60)}
        TERRAIN_BANDS = ((162, 420), (420, 950), (950, 2300))

        seg_start = bisect_left(self._seg_z0, sz - 400)  # z0 < sz-400 的段必然不可见
        for seg_idx in range(seg_start, len(self.track_data)):
            seg = self.track_data[seg_idx]
            cx0, cy0, z0, cx1, cy1, z1, seg_biome = seg
            sz0 = z0 - sz
            sz1 = z1 - sz
            if sz1 < -200:
                continue
            if sz0 > 3000:   # 升序排列，后面全部不可见
                break

            avg_z = (sz0 + sz1) // 2
            dist_factor = max(0, 255 - abs(avg_z) // 15)
            fade = min(1.0, max(0.0, (avg_z - 900) / 1800.0))   # 远处融进幕布色

            # ═══ 地形（路缘向外 3 圈丘陵带，颜色按主题区，远处融进幕布）═══
            tb = bd_terrain.get(seg_biome, backdrop)
            tr = tb[0] + (backdrop[0] - tb[0]) * fade
            tg = tb[1] + (backdrop[1] - tb[1]) * fade
            tb_ = tb[2] + (backdrop[2] - tb[2]) * fade
            for side in (-1, 1):
                if seg_biome == 1 and side == 1:
                    # 沙滩右侧：沙坡下探入海（陆地丘陵让位给海面）
                    gl.add_quad3d((cx0 + 162, cy0, z0), (cx0 + 300, cy0 - 14, z0),
                                  (cx1 + 300, cy1 - 14, z1), (cx1 + 162, cy1, z1),
                                  int(tr * 0.95), int(tg * 0.9), int(tb_ * 0.82))
                    continue
                for in_off, out_off in TERRAIN_BANDS:
                    y_in0 = self._terrain_y(cx0 + side * in_off, z0, cy0)
                    y_out0 = self._terrain_y(cx0 + side * out_off, z0, cy0)
                    y_in1 = self._terrain_y(cx1 + side * in_off, z1, cy1)
                    y_out1 = self._terrain_y(cx1 + side * out_off, z1, cy1)
                    band_b = 0.95 if in_off == 162 else (0.85 if in_off == 420 else 0.78)
                    gl.add_quad3d(
                        (cx0 + side * in_off, y_in0, z0),
                        (cx0 + side * out_off, y_out0, z0),
                        (cx1 + side * out_off, y_out1, z1),
                        (cx1 + side * in_off, y_in1, z1),
                        int(tr * band_b), int(tg * band_b), int(tb_ * band_b))

            # ═══ 路面颜色（公式与原版一致，参数仍由 Piet 拥有）═══
            bm_base = 3200 + seg_biome * 10
            r_coef = p.get(bm_base, 62)
            g_coef = p.get(bm_base+1, 60 + seg_biome*10)
            b_coef = p.get(bm_base+2, 68 - seg_biome*5)
            clamp_min = p.get(bm_base+3, 30)
            clamp_max = p.get(bm_base+4, 255)
            noise_seed = (seg_idx * 12347 + int(avg_z) * 7891) & 0x7FFF
            noise_amp = p.get(bm_base+5, 9)
            noise_coarse = (noise_seed % noise_amp) - (noise_amp // 2)
            noise_fine = ((noise_seed * 137 + 73) % 5) - 2
            stripe = int(math.sin(avg_z * 0.05) * 3)
            noise = noise_coarse + noise_fine + stripe
            light_str = p.get(3016, 3)
            turn_light = int((cx0 + cx1) * light_str // 100)

            r = _clamp(int(dist_factor * r_coef // 100) + noise + turn_light, clamp_min, clamp_max)
            g = _clamp(int(dist_factor * g_coef // 100) + noise, clamp_min, clamp_max)
            b2 = _clamp(int(dist_factor * b_coef // 100) + noise - turn_light//2, clamp_min, clamp_max)

            # 路面
            gl.add_quad3d((cx0 - ROAD_HALF, cy0, z0), (cx0 + ROAD_HALF, cy0, z0),
                          (cx1 + ROAD_HALF, cy1, z1), (cx1 - ROAD_HALF, cy1, z1),
                          r, g, b2)

            # ── 路面反光 ──
            ref_strength = p.get(3091, 0)
            if ref_strength > 0 and abs(avg_z) < 600:
                ref_seed = (seg_idx * 73 + int(avg_z) * 31) & 0xFF
                if ref_seed > 120:
                    ref_alpha = int(ref_strength * (ref_seed % 60) / 100)
                    offset = (ref_seed % 20) - 10
                    gl.add_quad3d((cx0 + offset, cy0 + 0.5, z0),
                                  (cx0 + offset + 15, cy0 + 0.5, z0),
                                  (cx1 + offset + 15, cy1 + 0.5, z1),
                                  (cx1 + offset, cy1 + 0.5, z1),
                                  min(255, r + 60), min(255, g + 60), min(255, b2 + 60), ref_alpha)

            # ═══ 路肩（边线外侧深色带，接地形起点）═══
            sh_r, sh_g, sh_b = r * 3 // 4, g * 3 // 4, b2 * 3 // 4
            gl.add_quad3d((cx0 - ROAD_HALF - 12, cy0 - 0.5, z0), (cx0 - ROAD_HALF, cy0 - 0.5, z0),
                          (cx1 - ROAD_HALF, cy1 - 0.5, z1), (cx1 - ROAD_HALF - 12, cy1 - 0.5, z1),
                          sh_r, sh_g, sh_b)
            gl.add_quad3d((cx0 + ROAD_HALF, cy0 - 0.5, z0), (cx0 + ROAD_HALF + 12, cy0 - 0.5, z0),
                          (cx1 + ROAD_HALF + 12, cy1 - 0.5, z1), (cx1 + ROAD_HALF, cy1 - 0.5, z1),
                          sh_r, sh_g, sh_b)

            # ═══ 边线（红/白交替，参数由 Piet 拥有）═══
            edge_w = p.get(3040, 8)
            e_base = 3240 + seg_biome * 6
            is_red = ((seg_idx // 3) & 1) == 0
            if is_red:
                er, eg, eb = p.get(e_base,255), p.get(e_base+1,50), p.get(e_base+2,50)
            else:
                er, eg, eb = p.get(e_base+3,255), p.get(e_base+4,255), p.get(e_base+5,255)
            edge_f = max(0.3, dist_factor / 200)
            er = int(er * edge_f); eg = int(eg * edge_f); eb = int(eb * edge_f)
            gl.add_quad3d((cx0 - ROAD_HALF, cy0 + 0.4, z0),
                          (cx0 - ROAD_HALF + edge_w, cy0 + 0.4, z0),
                          (cx1 - ROAD_HALF + edge_w, cy1 + 0.4, z1),
                          (cx1 - ROAD_HALF, cy1 + 0.4, z1), er, eg, eb)
            gl.add_quad3d((cx0 + ROAD_HALF - edge_w, cy0 + 0.4, z0),
                          (cx0 + ROAD_HALF, cy0 + 0.4, z0),
                          (cx1 + ROAD_HALF, cy1 + 0.4, z1),
                          (cx1 + ROAD_HALF - edge_w, cy1 + 0.4, z1), er, eg, eb)

            # ═══ 中线（贴路面小段）═══
            if avg_z > -50:
                m_base = p.get(3024, 80)
                for offset in [-30, 0, 30]:
                    t = 0.3 + (offset + 30) / 100
                    mx = cx0 + (cx1 - cx0) * t
                    my = cy0 + (cy1 - cy0) * t
                    mz_abs = z0 + (z1 - z0) * t
                    m_bright = int(m_base * dist_factor // 150)
                    if m_bright > 5:
                        gl.add_quad3d((mx - 2, my + 0.6, mz_abs - 20),
                                      (mx + 2, my + 0.6, mz_abs - 20),
                                      (mx + 2, my + 0.6, mz_abs + 20),
                                      (mx - 2, my + 0.6, mz_abs + 20),
                                      m_bright, m_bright, m_bright)

            # ═══ 护栏（内壁 + 顶盖，隔段立柱）═══
            rail_b = max(0.35, 1.0 - abs(avg_z) / 2500)
            rr = int(185 * rail_b); rg = int(190 * rail_b); rb_ = int(200 * rail_b)
            rh = 20.0
            for side in (-1, 1):
                rx0 = cx0 + side * (ROAD_HALF + 12)
                rx1 = cx1 + side * (ROAD_HALF + 12)
                gl.add_quad3d((rx0, cy0, z0), (rx0, cy0 + rh, z0),
                              (rx1, cy1 + rh, z1), (rx1, cy1, z1), rr, rg, rb_)
                cap_i0 = cx0 + side * (ROAD_HALF + 8); cap_o0 = cx0 + side * (ROAD_HALF + 15)
                cap_i1 = cx1 + side * (ROAD_HALF + 8); cap_o1 = cx1 + side * (ROAD_HALF + 15)
                gl.add_quad3d((cap_i0, cy0 + rh, z0), (cap_o0, cy0 + rh, z0),
                              (cap_o1, cy1 + rh, z1), (cap_i1, cy1 + rh, z1),
                              min(255, rr + 25), min(255, rg + 25), min(255, rb_ + 25))
                if seg_idx & 1 == 0:
                    post_x = cx0 + side * (ROAD_HALF + 11)
                    gl.add_quad3d((post_x - 3, cy0, z0), (post_x + 3, cy0, z0),
                                  (post_x + 3, cy0 + rh + 3, z0), (post_x - 3, cy0 + rh + 3, z0),
                                  int(150*rail_b), int(155*rail_b), int(165*rail_b))

        # ═══ 沙滩区海水（3D 水面 + 动态波光）═══
        for seg_idx in range(seg_start, len(self.track_data)):
            seg = self.track_data[seg_idx]
            cx0, cy0, z0, cx1, cy1, z1, seg_biome = seg
            if seg_biome != 1:
                continue
            sz0 = z0 - sz; sz1 = z1 - sz
            if sz1 < -200 or sz0 > 3000: continue
            avg_z = (sz0 + sz1) // 2
            wx0 = cx0 + ROAD_HALF + 150
            wx1 = cx1 + ROAD_HALF + 150
            wy0 = cy0 - 14
            wy1 = cy1 - 14
            w_bright = max(0.2, 1.0 - abs(avg_z) / 2000)
            w_r = int(30*w_bright); w_g = int(100*w_bright); w_b = int(200*w_bright)
            # 远处海面融进幕布色（与地形同一渐隐节奏）
            fade_w = min(1.0, max(0.0, (avg_z - 900) / 1800.0))
            w_r = int(w_r + (backdrop[0] - w_r) * fade_w)
            w_g = int(w_g + (backdrop[1] - w_g) * fade_w)
            w_b = int(w_b + (backdrop[2] - w_b) * fade_w)
            gl.add_quad3d((wx0, wy0, z0), (wx0 + 1950, wy0, z0),
                          (wx1 + 1950, wy1, z1), (wx1, wy1, z1), w_r, w_g, w_b)

            # 动态波光
            if abs(avg_z) < 600:
                for wi in range(6):
                    w_seed = (seg_idx * 31 + wi * 137 + int(self.scroll_z // 30)) & 0xFF
                    if w_seed > 140:
                        ft = wi / 6.0
                        sxp = wx0 + (wx1 - wx0) * ft + (w_seed % 1900)
                        szp = z0 + (z1 - z0) * ft
                        syp = wy0 + (wy1 - wy0) * ft + 0.5
                        sp_alpha = min(200, 80 + (w_seed % 120))
                        sp_size = max(2, 4 - abs(avg_z) // 200)
                        gl.add_quad3d((sxp - sp_size, syp, szp - sp_size),
                                      (sxp + sp_size, syp, szp - sp_size),
                                      (sxp + sp_size, syp, szp + sp_size),
                                      (sxp - sp_size, syp, szp + sp_size),
                                      240, 250, 255, sp_alpha)

        # ═══ 树木（3D：树干方柱 + 双层锥形树冠 / 棕榈）+ 贴地阴影 ═══
        sh_alpha = p.get(3034, 70)
        for item in self.tree_positions[bisect_left(self._tree_z, sz - 200):]:
            if len(item) >= 4:
                wx, wy, wz, tree_type = item
            else:
                wx, wy, wz = item; tree_type = 0
            twz = wz - sz
            if twz > 3000: break
            if twz < -220: continue
            t_base = self._terrain_y(wx, wz, self._road_cy(wz))
            t_bright = max(0.35, 1.0 - abs(twz) / 3000)

            # 贴地阴影（单个四边形替代原百来个小块）
            if sh_alpha > 0 and -200 < twz < 2500:
                sa = int(sh_alpha * max(0.3, 1.0 - abs(twz) / 2500))
                gl.add_quad3d((wx - 26, t_base + 0.4, wz - 10), (wx + 26, t_base + 0.4, wz - 10),
                              (wx + 30, t_base + 0.4, wz + 12), (wx - 22, t_base + 0.4, wz + 12),
                              0, 0, 0, sa)

            if tree_type == -1:  # 棕榈树：弯曲树干 + 放射叶
                trunk_c = (int(140*t_bright), int(100*t_bright), int(50*t_bright))
                leaf_c = (int(50*t_bright), int(160*t_bright), int(50*t_bright))
                for ti in range(3):
                    y_a = t_base + ti * 22.0
                    y_b = t_base + (ti + 1) * 22.0
                    ox_a = 1.5 * ti * ti
                    ox_b = 1.5 * (ti + 1) * (ti + 1)
                    gl.add_quad3d((wx + ox_a - 3, y_a, wz), (wx + ox_a + 3, y_a, wz),
                                  (wx + ox_b + 2, y_b, wz), (wx + ox_b - 2, y_b, wz), *trunk_c)
                top_x = wx + 1.5 * 9
                top_y = t_base + 66.0
                for a_deg in range(0, 360, 51):
                    a = math.radians(a_deg)
                    dx_, dz_ = math.cos(a) * 45.0, math.sin(a) * 45.0
                    mx_, mz_ = top_x + dx_ * 0.5, wz + dz_ * 0.5
                    ex_, ez_ = top_x + dx_, wz + dz_
                    gl.add_quad3d((top_x, top_y, wz), (mx_, top_y + 6, mz_),
                                  (ex_, top_y - 10, ez_), (mx_, top_y + 2, mz_), *leaf_c)
            else:  # 普通树：树干方柱 + 两层四棱锥树冠
                tr_r = p.get(3031, 101); tr_g = p.get(3032, 67); tr_b = p.get(3033, 33)
                self._box3d(gl, wx, wz, 7, 7, t_base, t_base + 16,
                            (int(tr_r*t_bright), int(tr_g*t_bright), int(tr_b*t_bright)))
                cr1 = (p.get(3025, 34), p.get(3026, 139), p.get(3027, 34))
                cr2 = (p.get(3028, 60), p.get(3029, 180), p.get(3030, 60))
                self._pyramid3d(gl, wx, wz, 40, t_base + 18, t_base + 54,
                                [int(c * t_bright) for c in cr1])
                self._pyramid3d(gl, wx, wz, 28, t_base + 34, t_base + 66,
                                [int(c * t_bright) for c in cr2])

        # ═══ 建筑（3D 盒体 + 屋顶 + 窗户；数据字段 x,z,w,h,d,side,type）═══
        b_enabled = p.get(3050, 1)
        if b_enabled and self.buildings:
            b_col_r = p.get(3051, 180); b_col_g = p.get(3052, 190); b_col_b = p.get(3053, 210)
            b_roof_r = p.get(3054, 100); b_roof_g = p.get(3055, 100); b_roof_b = p.get(3056, 120)
            b_win_r = p.get(3057, 255); b_win_g = p.get(3058, 255); b_win_b = p.get(3059, 200)

            for building in self.buildings[bisect_left(self._bld_z, sz - 200):]:
                bx3d, bz3d, bw3d, bh3d, bd3d, b_side, b_type = building[:7]
                twz = bz3d - sz
                if twz > 3000: break
                if twz < -260: continue
                b_bright = max(0.3, 1.0 - abs(twz) / 2500)
                base_y = self._terrain_y(bx3d, bz3d, self._road_cy(bz3d))
                y1 = base_y + bh3d

                if b_type == 3:  # 工厂烟囱（细高，红白顶带）
                    cc = (int(90*b_bright), int(80*b_bright), int(75*b_bright))
                    self._box3d(gl, bx3d, bz3d, bw3d, bw3d, base_y, y1, cc, top=True,
                                top_col=(int(60*b_bright), int(55*b_bright), int(50*b_bright)))
                    gl.add_quad3d((bx3d - bw3d/2 - 0.6, y1 - 8, bz3d - bw3d/2 - 0.6),
                                  (bx3d + bw3d/2 + 0.6, y1 - 8, bz3d - bw3d/2 - 0.6),
                                  (bx3d + bw3d/2 + 0.6, y1 - 4, bz3d - bw3d/2 - 0.6),
                                  (bx3d - bw3d/2 - 0.6, y1 - 4, bz3d - bw3d/2 - 0.6),
                                  int(180*b_bright), int(60*b_bright), int(50*b_bright))
                    continue

                if b_type == 4:  # 公园亭子：矮盒 + 红色四棱锥顶
                    pk = (int(p.get(3270, 220)*b_bright), int(p.get(3271, 200)*b_bright),
                          int(p.get(3272, 160)*b_bright))
                    self._box3d(gl, bx3d, bz3d, bw3d, bd3d, base_y, y1, pk, top=False)
                    self._pyramid3d(gl, bx3d, bz3d, bw3d * 0.8, y1, y1 + 15,
                                    (int(180*b_bright), int(60*b_bright), int(60*b_bright)))
                    continue

                # 主体盒（四面 + 顶盖，±x 面自动调暗）
                if b_type == 2:  # 工厂：工业配色
                    fc = (int(p.get(3264, 100)*b_bright), int(p.get(3265, 90)*b_bright),
                          int(p.get(3266, 80)*b_bright))
                else:
                    fc = (int(b_col_r*b_bright), int(b_col_g*b_bright), int(b_col_b*b_bright))
                self._box3d(gl, bx3d, bz3d, bw3d, bd3d, base_y, y1, fc, top=True,
                            top_col=(int(b_roof_r*b_bright), int(b_roof_g*b_bright),
                                     int(b_roof_b*b_bright)))

                if b_type == 2:  # 工厂：单坡顶棚
                    roof_c = (int(p.get(3267, 80)*b_bright), int(p.get(3268, 70)*b_bright),
                              int(p.get(3269, 60)*b_bright))
                    gl.add_quad3d((bx3d - bw3d/2, y1, bz3d - bd3d/2),
                                  (bx3d - bw3d/2, y1 + 12, bz3d - bd3d/2),
                                  (bx3d + bw3d/2, y1 + 12, bz3d + bd3d/2),
                                  (bx3d + bw3d/2, y1, bz3d + bd3d/2), *roof_c)

                # 窗户：正面(-z) + 朝路面侧面（略出墙面防深度冲突）
                win_c = (int(b_win_r*b_bright), int(b_win_g*b_bright), int(b_win_b*b_bright))
                front_z = bz3d - bd3d/2 - 0.8
                for wy2 in range(6, int(bh3d) - 6, 12):
                    for wx2 in range(int(-bw3d/2) + 6, int(bw3d/2) - 6, 15):
                        win_seed = (int(bx3d*100 + wy2*7 + wx2*13)) & 0xFF
                        if win_seed > 80:
                            wb = 0.6 + (win_seed % 40) / 100.0
                            gl.add_quad3d((bx3d + wx2, base_y + wy2, front_z),
                                          (bx3d + wx2 + 7, base_y + wy2, front_z),
                                          (bx3d + wx2 + 7, base_y + wy2 + 7, front_z),
                                          (bx3d + wx2, base_y + wy2 + 7, front_z),
                                          min(255, int(win_c[0]*wb)),
                                          min(255, int(win_c[1]*wb)),
                                          min(255, int(win_c[2]*wb)))
                side_x = (bx3d - bw3d/2 - 0.8) if b_side > 0 else (bx3d + bw3d/2 + 0.8)
                for wy2 in range(6, int(bh3d) - 6, 14):
                    for wz2 in range(int(-bd3d/2) + 6, int(bd3d/2) - 6, 15):
                        win_seed = (int(bx3d*77 + wy2*5 + wz2*11)) & 0xFF
                        if win_seed > 90:
                            wb = 0.6 + (win_seed % 40) / 100.0
                            gl.add_quad3d((side_x, base_y + wy2, bz3d + wz2),
                                          (side_x, base_y + wy2, bz3d + wz2 + 7),
                                          (side_x, base_y + wy2 + 7, bz3d + wz2 + 7),
                                          (side_x, base_y + wy2 + 7, bz3d + wz2),
                                          min(255, int(win_c[0]*wb)),
                                          min(255, int(win_c[1]*wb)),
                                          min(255, int(win_c[2]*wb)))

        # ═══ 提交 3D 批次（深度排序由深度缓冲完成）═══
        gl.flush3d()
        gl.end_3d()

        # ═══ 后期处理纹理（2D 通道叠加）═══
        gl.draw_texture('fog', alpha=p.get(3004, 150))
        gl.draw_texture('vignette', alpha=255)
        ray_alpha = p.get(3090, 60)
        if ray_alpha > 0:
            gl.draw_texture('ray', alpha=ray_alpha)

        # ═══ 粒子（落叶） ═══
        for leaf in self._leaves:
            x, y, _, _, rot, size_mult, life = leaf
            if x < -50 or x > SW+50 or y < -50 or y > SH+50: continue
            size = max(2, int(4 * size_mult))
            life_ratio = life / 150.0
            lr = int(180 * life_ratio); lg = int(120 * life_ratio); lb = int(40 * life_ratio)
            gl.add_quad(x-size, y-size//2, x+size, y-size//2,
                        x+size, y+size//2, x-size, y+size//2, lr, lg, lb)
        # ═══ P3: HUD 仪表（速度表/转速盘/小地图）——数值全来自 persist ═══
        emit_hud_gauges(_GLPoly(gl), p, self._cx_at, sz, SW, SH)
        gl.flush()

        # ═══ HUD 文本已由 Piet 的 EXT_HUD 命令绘制（P3），此处不再硬编码 ═══
        # 注意：此处不再调用 pygame.display.flip()——翻转由 Piet 发出的 swap 命令统一执行，
        # 避免每帧双重翻转导致呈现未绘制的后备缓冲（黑屏根因）

    def run(self):
        """主循环：每帧重置 VM → 填充运行时状态 → 执行 Piet 程序 → 傻执行命令"""
        try:
            # 先执行一次 Piet 程序，触发 CREATE_WIN（创建窗口）
            init_done = False
            init_attempts = 0
            while self.running and not init_done and init_attempts < 3:
                init_attempts += 1
                self.vm.reset()
                cmds = self.vm.run()
                # M2: 首帧 INIT 完成后立即物化世界数据（本帧 draw_track 就要用）
                if not self._world_ready and self.vm.runtime['persist'].get(69003) == 1:
                    self._materialize_world()
                if not cmds:
                    continue
                for cmd in cmds:
                    self._exec_cmd(cmd)
                    if cmd[0] == 'create_win' and self.screen is not None:
                        init_done = True

            if init_done:
                print(f"[启动] Phase C 就绪，正在运行...", flush=True)

            if self.screen is None:
                print("[错误] Piet 程序未创建窗口，退出", flush=True)
                # 调试：打印最后几次尝试的命令
                print(f"[调试] 最后一次 cmds: {cmds}", flush=True)
                if pygame.get_init():
                    pygame.quit()
                return

            # 清空残留事件，防止误触发退出
            pygame.event.clear()

            # 测试钩子：PIET_SEEK_Z 跳转到指定赛道位置（自动化验证用）
            seek_z = os.environ.get('PIET_SEEK_Z')
            if seek_z:
                self.scroll_z = int(seek_z) % 100000
                self.vm.runtime['persist'][2001] = self.scroll_z
                print(f'[验证] 跳转 scroll_z = {self.scroll_z}', flush=True)

            # 正式主循环（每帧重置 VM，重新执行全部 Piet 指令）
            while self.running:
                # 1. 事件轮询（仅处理退出事件）
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        self.quit_flag = 1
                    elif event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_ESCAPE:
                            self.quit_flag = 1
                        # ⚠ 速度由 Piet 控制，Python 不再处理 K_UP/K_DOWN

                # 持续按键检测 → 映射到 keys 数组让 Piet 读取
                keys = pygame.key.get_pressed()
                self.vm.runtime['keys'][200] = 1 if keys[pygame.K_LEFT] else 0
                self.vm.runtime['keys'][201] = 1 if keys[pygame.K_RIGHT] else 0
                self.vm.runtime['keys'][202] = 1 if keys[pygame.K_UP] else 0
                self.vm.runtime['keys'][203] = 1 if keys[pygame.K_DOWN] else 0
                # 自动化验证：模拟转向键盘输入（Piet 仍拥有位置决策，这里只喂键盘）
                if os.environ.get('PIET_DEMO_STEER'):
                    fc = self.frame_count
                    if 8 <= fc < 20:
                        self.vm.runtime['keys'][201] = 1   # 右
                    elif 26 <= fc < 38:
                        self.vm.runtime['keys'][200] = 1   # 左

                # 2. 填充运行时状态
                self.vm.runtime['ticks'] = pygame.time.get_ticks()
                if self.screen:
                    try:
                        self.vm.runtime['mouse_x'], self.vm.runtime['mouse_y'] = pygame.mouse.get_pos()
                    except:
                        self.vm.runtime['mouse_x'] = self.vm.runtime['mouse_y'] = 0
                    self.vm.runtime['screen_w'] = self.screen.get_width()
                    self.vm.runtime['screen_h'] = self.screen.get_height()
                self.vm.runtime['quit'] = self.quit_flag

                # 3. 重置 VM（回到程序起点），重新执行全部 Piet 指令
                self.vm.reset()
                cmds = self.vm.run()
                # M2: 世界数据物化钩子（守卫分支正常时首帧已物化，此处兜底）
                if not self._world_ready and self.vm.runtime['persist'].get(69003) == 1:
                    self._materialize_world()

                # 4. 傻执行 Piet 的指令
                for cmd in cmds:
                    self._exec_cmd(cmd)

                # 5. 限帧
                self.clock.tick(60)

                # 6. 检查退出
                if self.quit_flag:
                    self.running = False

            print("[退出] 游戏结束", flush=True)
            if pygame.get_init():
                pygame.quit()
        except Exception as e:
            print(f"[异常] {e}", flush=True)
            import traceback
            traceback.print_exc()
            if pygame.get_init():
                pygame.quit()
            sys.exit(1)


# ====================================================================
#  Phase C — 3D 赛道 + 障碍物 + 玩家操控 + 碰撞检测
#  用汇编器生成 game_core.png
#  所有的 PUSH 用紧凑编码避免超宽色块
# ====================================================================
def PC(a, v):
    """简写：紧凑 PUSH"""
    a.push_compact(v)

def _clamp(v, lo, hi):
    return max(lo, min(hi, v))

def _project(wx, wy, wz, cam_z0, cam_h, fov, sw, sh):
    """3D → 2D 透视投影，返回屏幕坐标 (sx, sy)"""
    rz = wz - cam_z0
    depth = max(rz, 1)
    scale = (fov * 100) // depth
    sx = sw // 2 + (wx * scale) // 100
    sy = sh // 2 - ((wy - cam_h) * scale) // 100
    return (sx, sy)

HUD_KEY0 = 61000   # HUD 文本模板基址 msg1「速度」（P3：Piet INIT 写入 UTF-8 字节流）
HUD_KEY1 = 61200   # HUD 文本模板基址 msg2「计时板」（P3-1）


def decode_hud_template(p, base=HUD_KEY0):
    """P3: 把 Piet 写进 persist 的 HUD 模板字节流解码成 Python 格式串。

    编码：UTF-8 字节逐个存 persist[base+i]，0 终止；
          0xFF（UTF-8 中的非法字节）作数值占位符哨兵 → 解码为 '{}'。
    例：b'\\xe9\\x80\\x9f\\xe5\\xba\\xa6: \\xff  |  ...' → '速度: {}  |  ...'
    """
    raw = bytearray()
    i = base
    while True:
        v = p.get(i)
        if not v:
            break
        raw.append(v & 0xFF)
        i += 1
    out, buf = [], bytearray()
    for b in raw:
        if b == 0xFF:
            out.append(buf.decode('utf-8'))
            buf = bytearray()
            out.append('{}')
        else:
            buf.append(b)
    out.append(buf.decode('utf-8'))
    return ''.join(out)


# ====================================================================
#  P3 呈现层：仪表与地图
#  几何/配色/位置 = 固定渲染模板（不含游戏决策）；数值全部来自 persist。
#  draw.add_polygon(pts, r, g, b, a=255) —— GL 与 2D 各实现一份，几何只写一遍。
# ====================================================================

class _GLPoly:
    """仪表几何 → GL 2D 批次（与雾/暗角/粒子同一条 flush 通路）"""

    def __init__(self, gl):
        self.gl = gl

    def add_polygon(self, pts, r, g, b, a=255):
        self.gl.add_polygon(pts, r, g, b, a)


class _PyPoly:
    """仪表几何 → pygame.Surface（2D 回退通道，不透明绘制）"""

    def __init__(self, surf):
        self.surf = surf

    def add_polygon(self, pts, r, g, b, a=255):
        pygame.draw.polygon(self.surf, (int(r), int(g), int(b)),
                            [(int(x), int(y)) for x, y in pts])


def _circle_pts(cx, cy, r, n=24):
    return [(cx + math.cos(2 * math.pi * i / n) * r,
             cy + math.sin(2 * math.pi * i / n) * r) for i in range(n)]


def _dial(draw, cx, cy, r, val, vmax, accent):
    """指针式刻度盘：底盘 + 填充弧 + 刻度 + 指针 + 中心帽。
    val 由 Piet 给出（此处只钳位防越界），270° 扫角从 135° 起。"""
    a0, sweep = math.radians(135.0), math.radians(270.0)
    draw.add_polygon(_circle_pts(cx, cy, r), 26, 28, 36)
    draw.add_polygon(_circle_pts(cx, cy, r - 3, 24), 44, 48, 58)
    # 填充弧：30 段，已到值亮、未到值暗
    frac = 0.0 if vmax <= 0 else max(0.0, min(1.0, val / float(vmax)))
    seg, r_in = 30, r * 0.86
    for i in range(seg):
        f1 = (i + 1) / float(seg)
        b0, b1 = a0 + sweep * (i / float(seg)), a0 + sweep * f1
        col = accent if f1 <= frac + 1e-9 else (64, 68, 80)
        draw.add_polygon([(cx + math.cos(b0) * r_in, cy + math.sin(b0) * r_in),
                          (cx + math.cos(b0) * r, cy + math.sin(b0) * r),
                          (cx + math.cos(b1) * r, cy + math.sin(b1) * r),
                          (cx + math.cos(b1) * r_in, cy + math.sin(b1) * r_in)], *col)
    # 刻度：11 根（每 5 根一根长的）
    for i in range(11):
        ang = a0 + sweep * (i / 10.0)
        ca, sa = math.cos(ang), math.sin(ang)
        major = (i % 5 == 0)
        r0, r1 = r * (0.58 if major else 0.68), r * 0.82
        w = 1.8 if major else 1.0
        px, py = -sa * w, ca * w
        draw.add_polygon([(cx + ca * r0 + px, cy + sa * r0 + py),
                          (cx + ca * r1 + px, cy + sa * r1 + py),
                          (cx + ca * r1 - px, cy + sa * r1 - py),
                          (cx + ca * r0 - px, cy + sa * r0 - py)], 188, 194, 204)
    # 指针
    ang = a0 + sweep * frac
    ca, sa = math.cos(ang), math.sin(ang)
    ln, w = r * 0.74, 2.8
    px, py = -sa * w, ca * w
    draw.add_polygon([(cx + px, cy + py), (cx + ca * ln + px, cy + sa * ln + py),
                      (cx + ca * ln - px, cy + sa * ln - py), (cx - px, cy - py)],
                     accent[0], accent[1], accent[2])
    draw.add_polygon(_circle_pts(cx, cy, 4.5, 10), 22, 24, 30)


def _minimap(draw, cx_of, scroll_z, x0, y0, marker_col, w=124, h=146):
    """全圈小地图：z 0→100000 映射纵向，cx∈[−250,250] 映射横向。
    中心线取自 Piet 写入的 cx 表；玩家点用车辆当前配色（碰撞时自动变紫红）。"""
    draw.add_polygon([(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)], 24, 26, 34)
    draw.add_polygon([(x0 + 2, y0 + 2), (x0 + w - 2, y0 + 2),
                      (x0 + w - 2, y0 + h - 2), (x0 + 2, y0 + h - 2)], 40, 44, 56)
    ix0, ix1, iy0, iy1 = x0 + 8, x0 + w - 8, y0 + 6, y0 + h - 6
    mx_c = (ix0 + ix1) * 0.5
    half = (ix1 - ix0) * 0.5

    def pt(z):
        return (mx_c + cx_of(z) / 250.0 * half,
                iy0 + (iy1 - iy0) * (z % 100000) / 100000.0)

    n = 160
    prev = pt(0)
    for i in range(1, n + 1):
        cur = pt(i * 100000 // n)
        draw.add_polygon([(prev[0] - 1.25, prev[1]), (prev[0] + 1.25, prev[1]),
                          (cur[0] + 1.25, cur[1]), (cur[0] - 1.25, cur[1])],
                         148, 156, 170)
        prev = cur
    # 起/终点线（z = 0，即 100000 回绕处）
    sx, sy = pt(0)
    draw.add_polygon([(ix0, sy - 1.2), (ix1, sy - 1.2),
                      (ix1, sy + 1.2), (ix0, sy + 1.2)], 238, 238, 238)
    # 玩家点（深色描边 + 车色菱形）
    px, py = pt(scroll_z)
    draw.add_polygon([(px - 5, py), (px, py - 6), (px + 5, py), (px, py + 6)], 18, 18, 22)
    draw.add_polygon([(px - 3.5, py), (px, py - 4.5),
                      (px + 3.5, py), (px, py + 4.5)], *marker_col)


def emit_hud_gauges(draw, p, cx_of, scroll_z, sw=800, sh=600):
    """P3 呈现层：速度表 / 转速盘 / 小地图。

    数值全部来自 persist：2002 速度、3041-3043 车体色、50000+ 赛道 cx 表、2001 位置。
    几何/配色/位置是固定渲染模板——不含游戏决策（M5 三分法里的"呈现"那一类）。
    """
    speed = p.get(69112, p.get(2002, 0))
    _dial(draw, 108, sh - 100, 56, speed, 45, (255, 206, 64))          # 速度表 0..45
    _dial(draw, 254, sh - 100, 56, speed * 130, 6000, (255, 96, 72))   # 转速盘 0..6000
    _minimap(draw, cx_of, scroll_z, x0=sw - 144, y0=sh - 164,
             marker_col=(p.get(3041, 255), p.get(3042, 60), p.get(3043, 60)))


def emit_hit(a, lib, wx_key):
    """M3 碰撞：单棵树判定体（模块级，便于单测复用）。

    栈约定：入口 [pid]，出口 [pid · NOT(cond)]。
    cond = rng · hit，rng = (twz ≥ 0)·(twz ≤ 3000)，hit = (radius·twz)² > (d·fov)²，
    d = wx − car_wx。依赖 persist：3035 半径、3038 FOV、69010 car_wx、
    69016 twz、wx_key（69012/69013 = 该格右/左树 x）。"""
    PC(a, 3035); a.emit('IN_N'); lib.peek(69016); a.emit('MULTIPLY')
    a.emit('DUPLICATE'); a.emit('MULTIPLY')                        # [pid, R²]
    lib.peek(wx_key); PC(a, 69010); a.emit('IN_N'); a.emit('SUBTRACT')
    PC(a, 3038); a.emit('IN_N'); a.emit('MULTIPLY')                # [pid, R², d·fov]
    a.emit('DUPLICATE'); a.emit('MULTIPLY')                        # [pid, R², (d·fov)²]
    a.emit('GREATER')                                              # [pid, hit]
    # rng = (twz ≥ 0)·(twz ≤ 3000)
    lib.peek(69016); a.emit('DUPLICATE')
    PC(a, 3000); a.emit('GREATER'); a.emit('NOT')                  # [pid, hit, twz, le]
    lib.roll(1, 2)                                                 # [pid, hit, le, twz]
    PC(a, 1); a.emit('ADD'); a.push_zero(); a.emit('GREATER')      # [pid, hit, le, ge]
    a.emit('MULTIPLY')                                             # [pid, hit, rng]
    a.emit('MULTIPLY')                                             # [pid, cond]
    a.emit('NOT'); a.emit('MULTIPLY')                              # [pid · NOT(cond)]


def _emit_road(a, lib, z_off, amp, wavelen):
    """[栈中性] → [road]；road = sin((sz+z_off)·1000//wavelen)·amp//1000，sz 读 persist[2001]。

    与 Python 侧 _road_cx/_road_cy 同源（int(sin(z/λ)·amp)）：DIVIDE 为 floor，
    定点偏差 ≤2。z_off<0 时角为负——sin 宏经 MOD 归约 / f=(x2>π) 符号折叠后自动正确。"""
    PC(a, 2001); a.emit('IN_N')            # [sz]
    if z_off > 0:
        PC(a, z_off); a.emit('ADD')
    elif z_off < 0:
        PC(a, -z_off); a.emit('SUBTRACT')  # [z]
    PC(a, 1000); a.emit('MULTIPLY')
    PC(a, wavelen); a.emit('DIVIDE')       # [z·1000//λ]
    lib.sin()                              # [1000·sin]
    PC(a, amp); a.emit('MULTIPLY')
    PC(a, 1000); a.emit('DIVIDE')          # [road]


def emit_timing(a, lib):
    """P3：计时（总用时 ms）。全在 Piet 帧段算出（栈中性：入口/出口皆空）。

    数据源 IN_N 1000 = pygame 毫秒时钟（Runtime 每帧填入 runtime['ticks']）。
    t0 惰性初始化：t0' = t0 + (tick − t0)·(t0 == 0)——
      t0==0 → 取本帧 tick；否则保持原值。
    **不能把 t0 写在 INIT 段**：INIT 在 pygame 初始化之前执行（见 PietRuntime.run
    的引导段），那时时钟恒为 0，写进去等于没写。

      69100 t0'（0 表示尚未起跑）   69102 elapsed = tick − t0'
    """
    lib.peek(69100)                        # [t0]
    a.emit('DUPLICATE')                    # [t0, t0]
    a.emit('NOT')                          # [t0, is0]        is0 = (t0 == 0)
    lib.peek(1000)                         # [t0, is0, t]
    lib.peek(69100)                        # [t0, is0, t, t0]
    a.emit('SUBTRACT')                     # [t0, is0, t−t0]
    a.emit('MULTIPLY')                     # [t0, is0·(t−t0)]
    a.emit('ADD')                          # [t0']（is0=0 时即原 t0）
    lib.put(69100)
    lib.peek(1000)                         # [t]
    lib.peek(69100)                        # [t, t0']
    a.emit('SUBTRACT')                     # [elapsed]
    lib.put(69102)


def emit_cam_pose(a, lib):
    """M4：追尾相机姿态 + 玩家车世界 y，全在 Piet 帧段算出（栈中性：入口/出口皆空）。

    调用点必须在 EXT_DRAW_TRACK 之前——渲染读的是 persist[2001]（上帧末 handle_input
    同步来的 self.scroll_z），而 M3 积分在帧段末尾才改写它，故此处姿态与渲染的 sz 同源。
      cam_x  69020 = road_cx(sz−150)     cam_y  69022 = road_cy(sz−150) + cam_h(3039)
      cam_z  69024 = sz − 220            look_x 69026 = road_cx(sz+400)
      look_y 69028 = cam_y − 12          look_z 69030 = sz + 400
      car_wy 69032 = road_cy(sz)         （car_wx 69010 由 M3-B 写，car_wz = sz）
    """
    _emit_road(a, lib, -150, 250, 1200); lib.put(69020)          # cam_x
    _emit_road(a, lib, -150, 40, 600)                            # → road_cy(sz-150)
    PC(a, 3039); a.emit('IN_N'); a.emit('ADD'); lib.put(69022)   # cam_y = +cam_h
    PC(a, 2001); a.emit('IN_N'); PC(a, 220); a.emit('SUBTRACT'); lib.put(69024)
    _emit_road(a, lib, 400, 250, 1200); lib.put(69026)           # look_x
    lib.peek(69022); PC(a, 12); a.emit('SUBTRACT'); lib.put(69028)
    PC(a, 2001); a.emit('IN_N'); PC(a, 400); a.emit('ADD'); lib.put(69030)
    _emit_road(a, lib, 0, 40, 600); lib.put(69032)               # car_wy


ROAD_YAW_KEY = 69038      # P4: 路面切线偏航角（毫弧度，S=ROAD_YAW_SCALE，可为负）
ROAD_YAW_SCALE = 1000


def _emit_road_yaw(a, lib):
    """[P4; 栈中性] → []；persist[ROAD_YAW_KEY] = 路面切线偏航角（毫弧度）。

    road_yaw = atan2(road_cx(sz+40) − road_cx(sz−40), 80)
             = atan(250·[sin((sz+40)/1200) − sin((sz−40)/1200)] / 80)
             = atan(0.2083333·cos(sz/1200))            ← 和差化积（精确恒等）

    定点实现（与 sin 宏同源，S=1000）：
      cosF = sin(sz·1000//1200 + 1571)                （1571 = π/2 毫弧度）
      yaw  = 5·cosF//24 − (cosF²//10³)·cosF//10³·3//10³   （atan 二阶 Taylor）
    残差：三阶项 t⁵/5 ≤ 0.09 mrad（|t| ≤ 0.2083 → 鼻端横向 ≲0.03 世界单位，亚像素）。
    旧 Python 版取 floor 量化差分，端点各自 ±1 → yaw 抖动 ≤12.5 mrad；本式连续无抖动。

    必须在 EXT_DRAW_TRACK 之前——读的 persist[2001] 是"积分前"的 sz，与相机/渲染同源。
    """
    PC(a, 2001); a.emit('IN_N')
    PC(a, 1000); a.emit('MULTIPLY')
    PC(a, 1200); a.emit('DIVIDE')
    PC(a, 1571); a.emit('ADD')                   # [arg]  cos 的自变量
    lib.sin()                                    # [cosF]  = cos(sz/1200)·1000
    a.emit('DUPLICATE')                          # [cosF, cosF]
    PC(a, 5); a.emit('MULTIPLY')
    PC(a, 24); a.emit('DIVIDE')                  # [cosF, term1]  = t·1000（一阶项，mrad）
    lib.roll(1, 2)                               # [term1, cosF]
    a.emit('DUPLICATE'); a.emit('DUPLICATE'); a.emit('MULTIPLY')
    PC(a, 1000); a.emit('DIVIDE')                # [term1, cosF, c2]
    a.emit('MULTIPLY')
    PC(a, 1000); a.emit('DIVIDE')                # [term1, c3]
    PC(a, 3); a.emit('MULTIPLY')
    PC(a, 1000); a.emit('DIVIDE')                # [term1, corr]
    a.emit('SUBTRACT')                           # [yaw]
    lib.put(ROAD_YAW_KEY)


CAR_ROLL_KEY = 6200        # P5: 侧倾角（毫弧度，S=CAR_POSE_SCALE，|roll| ≤ 140）
CAR_STEER_KEY = 6202       # P5: 前轮转向角（毫弧度，|steer| ≤ 450）
CAR_SPIN_KEY = 6204        # P5: 轮自转角（毫弧度）= 积分前 sz·100（与渲染 sz 同源，滞后一帧）
CAR_PREVWX_KEY = 6206      # P5: 上帧 car_wx（差分滤波状态）
CAR_POSE_SEED_KEY = 6208   # P5: 姿态滤波播种标志（0=未播种 → 首帧 dx=0，同旧 prev_wx=None）
CAR_POSE_SCALE = 1000


def _emit_clamp(a, lo, hi):
    """[v] → [clamp(v, lo, hi)]；整数，lo ≤ hi。Piet 无 MIN/MAX，用恒等式展开。"""
    PC(a, lo); a.emit('SUBTRACT')                                  # [v-lo]
    a.emit('DUPLICATE'); a.push_zero(); a.emit('GREATER'); a.emit('MULTIPLY')   # [max(v-lo,0)]
    a.emit('DUPLICATE')                                            # [w, w]
    PC(a, hi - lo); a.emit('SUBTRACT')                             # [w, w-M]
    a.emit('DUPLICATE'); a.push_zero(); a.emit('GREATER'); a.emit('MULTIPLY')   # [w, max(0,w-M)]
    a.emit('SUBTRACT')                                             # [min(w,M)]
    PC(a, lo); a.emit('ADD')                                       # [clamp]


def _emit_car_spin(a, lib):
    """[P5; 栈中性] → []；persist[CAR_SPIN_KEY] = 轮自转角（毫弧度）= sz·100。

    与旧 Python `spin = wz/10.0` 严格同源：wz 是渲染期 self.scroll_z，等于“本帧积分前”的
    sz。故本段必须在 M3-A 前向积分（覆盖 persist[2001]）之前发射，否则快一帧。
    """
    PC(a, 2001); a.emit('IN_N')            # [sz]
    PC(a, 100); a.emit('MULTIPLY')         # [sz·100]
    lib.put(CAR_SPIN_KEY)


def emit_car_pose(a, lib):
    """[P5; 栈中性] → []；Piet 算载具姿态 —— 侧倾 roll / 前轮转向 steer（毫弧度）。

    与旧 Python `_car_dyn` 滤波同构（整型定点，S=CAR_POSE_SCALE）：
        dx    = car_wx - prev_wx                        （首帧未播种 → dx = 0，同旧 prev_wx=None）
        roll  = roll·3/4  + clamp(dx·10, ±140)/4        （roll  ∈ [-140,140] mrad）
        steer = steer·7/10 + clamp(dx·20, ±450)·3/10   （steer ∈ [-450,450] mrad）

    依赖 persist：69010 本帧 car_wx、6206 上帧 car_wx、6208 播种标志、6200/6202 旧姿态。
    必须在 M3-B（写 69010）之后发射——本帧 car_wx 才可见。
    残差：整数 floor 与 Python 浮点的差异 ≤1 mrad（|roll|≤0.14 rad，车身亚像素）。
    """
    lib.peek(CAR_POSE_SEED_KEY)                                    # [sd]
    lib.peek(69010); lib.peek(CAR_PREVWX_KEY); a.emit('SUBTRACT')  # [sd, dx]
    a.emit('MULTIPLY')                                             # [d = dx·sd]
    a.emit('DUPLICATE')                                            # [d, d]
    PC(a, 10); a.emit('MULTIPLY')                                  # [d, d·10]
    _emit_clamp(a, -140, 140)                                      # [d, Tr]
    lib.peek(CAR_ROLL_KEY)                                         # [d, Tr, R]
    PC(a, 3); a.emit('MULTIPLY'); a.emit('ADD')                    # [d, 3R+Tr]
    PC(a, 4); a.emit('DIVIDE'); lib.put(CAR_ROLL_KEY)              # [d]
    PC(a, 20); a.emit('MULTIPLY')                                  # [Ts_raw]
    _emit_clamp(a, -450, 450); PC(a, 3); a.emit('MULTIPLY')        # [3Ts]
    lib.peek(CAR_STEER_KEY); PC(a, 7); a.emit('MULTIPLY')          # [3Ts, 7S]
    a.emit('ADD'); PC(a, 10); a.emit('DIVIDE'); lib.put(CAR_STEER_KEY)   # []
    lib.peek(69010); lib.put(CAR_PREVWX_KEY)                       # 播种上帧 wx
    lib.put_val(1, CAR_POSE_SEED_KEY)                              # 标记已播种


def build_game_core(codel_size=1):
    """生成 Phase C — M2：世界数据全部由 Piet INIT 段写入 persist（数值进图片）
    返回: (Piet 程序图片, [], [], [])  —— 数据由 PietRuntime 首帧后从 persist 物化

    persist 键位表（M2 定稿）：
      2000 玩家x  2001 scroll  2002 速度  202/203 上/下键  2100 碰撞
      M3 帧内暂存（每帧覆写）：69010 car_wx  69012/69013 树 wx(右/左)
                               69014 碰撞窗口起点 a0  69016 twz
      P3 计时/圈数（每帧覆写，emit_timing 段）：69100 起始 ticks（0=未初始化）
              69102 总用时 ms  69104 当前圈号（INIT 置 1，过线 +1）  69110 过线标志（0/1）
              69112 本帧 HUD/仪表速度快照（= 本帧开始时的 2002，速度更新前发布）
      P5 载具姿态（帧段每帧覆写）：6200 侧倾 6202 前轮转向 6204 轮自转（毫弧度，S=1000）
              6206 上帧 car_wx   6208 滤波播种标志（INIT 全置 0 → 首帧 dx=0）
      M4 相机姿态（每帧覆写，在 EXT_DRAW_TRACK 之前算）：69020 cam_x  69022 cam_y
              69024 cam_z  69026 look_x  69028 look_y  69030 look_z  69032 car_wy
      P4 车辆偏航（每帧覆写，同上时机）：69038 road_yaw（毫弧度，S=1000，可负）
      3036 基础速度(常量)  3038 FOV  3039 相机高  3037 路半宽  3035 碰撞半径
      3000-3091 渲染常量  3200-3353 主题区色板
      4100-4105 sin 数学常数  4106-4108 LCG 常数(A,C,M)  69003 INIT 完成标志
      7000-7019 地形模板参数（定点标度见 emit 处注释）
      50000+i     赛道 cx 表（i=0..500，z=200i）
      50600+i     赛道 cy 表（i=0..500）          51999 段数
      55000/56000 树楼共用 cx/cy 表(k=0..498, z=200k+100, k≡1 mod 6 无消费者)
      60000+i     生物群系表（i=0..500，biome=(i·200//25000)&3；M5 由 Piet 写入，运行时只查表）
      61000+i     HUD 文本模板（P3）：UTF-8 字节流，0 终止，0xFF=数值占位符（解码成 '{}'）
      65000+组*8       建筑槽值（值主序 d,d,h,h,w,w,u,u；188 组，同 z 两侧同值；x/z/side/type/烟囱由物化按模板推导）
    """
    a = PietAssembler()
    from piet_asm import PietLib   # 惰性导入（piet_asm 反向依赖本模块，避免循环）
    lib = PietLib(a)

    SW, SH = 800, 600
    SEG_LEN = 200; NUM_SEGS = 500; Z_PER_BIOME = 25000

    # ============ INIT 守卫：69003>0 → 跳过 INIT 直达帧段 ============
    PC(a, 69003); a.emit('IN_N')          # [f]（首帧 persist 空 → 0）
    a.push_zero()                         # 真 0（push(0) 实际压 1，见 push_zero）
    a.emit('GREATER')                     # flag = f > 0
    lib.skip_if()

    # ============ INIT 段（首帧执行一次，全部数值在此写入图片运行时） ============
    lib.emit_sin_constants()
    lib.emit_lcg_constants()

    # ── 1) 渲染常量（原 EXT_INIT_ALL/game_params 表，连续键批写）──
    game_params = [
        (3000, 80), (3001, 200), (3002, 210), (3003, 220), (3004, 150),
        (3005, 50), (3006, 100), (3007, 180), (3008, 135), (3009, 206), (3010, 235),
        (3011, 62), (3012, 63), (3013, 68), (3014, 30), (3015, 255),
        (3016, 3), (3017, 9),
        (3018, 255), (3019, 50), (3020, 50),
        (3021, 255), (3022, 255), (3023, 255),
        (3024, 80),
        (3025, 34), (3026, 139), (3027, 34),
        (3028, 60), (3029, 180), (3030, 60),
        (3031, 101), (3032, 67), (3033, 33),
        (3034, 70), (3035, 30), (3036, 8),
        (3037, 150), (3038, 250), (3039, 80),
        (3040, 8), (3041, 255), (3042, 60), (3043, 60),
        (3044, 180), (3045, 0), (3046, 120),
        (3047, 255), (3048, 255), (3049, 100),
        (3050, 1), (3051, 180), (3052, 190), (3053, 210),
        (3054, 100), (3055, 100), (3056, 120),
        (3057, 255), (3058, 255), (3059, 200),
        (3060, 8), (3061, 10), (3062, 6),
        (3200, 62), (3201, 63), (3202, 68), (3203, 30), (3204, 255), (3205, 9),
        (3210, 80), (3211, 72), (3212, 55), (3213, 40), (3214, 255), (3215, 8),
        (3220, 50), (3221, 52), (3222, 55), (3223, 20), (3224, 220), (3225, 12),
        (3230, 55), (3231, 60), (3232, 50), (3233, 35), (3234, 245), (3235, 7),
        (3240, 255), (3241, 50), (3242, 50), (3243, 255), (3244, 255), (3245, 255),
        (3246, 200), (3247, 150), (3248, 50), (3249, 255), (3250, 240), (3251, 200),
        (3252, 180), (3253, 180), (3254, 60), (3255, 200), (3256, 200), (3257, 200),
        (3258, 100), (3259, 200), (3260, 100), (3261, 255), (3262, 255), (3263, 255),
        (3264, 100), (3265, 90), (3266, 80), (3267, 80), (3268, 70), (3269, 60),
        (3270, 220), (3271, 200), (3272, 160),
        (3330, 50), (3331, 100), (3332, 180), (3333, 135), (3334, 206), (3335, 235),
        (3336, 80), (3337, 150), (3338, 220), (3339, 180), (3340, 220), (3341, 240),
        (3342, 60), (3343, 60), (3344, 80), (3345, 120), (3346, 120), (3347, 130),
        (3348, 40), (3349, 120), (3350, 100), (3351, 160), (3352, 220), (3353, 180),
        (3070, 30), (3071, 30), (3072, 30),
        (3073, 150), (3074, 200), (3075, 255),
        (3076, 200), (3077, 50), (3078, 50),
        (3090, 60),
        (3091, 0),
    ]
    runs = []
    cur = [game_params[0]]
    for kv in game_params[1:]:
        if kv[0] == cur[-1][0] + 1:
            cur.append(kv)
        else:
            runs.append(cur)
            cur = [kv]
    runs.append(cur)
    for run in runs:
        lib.write_consts(run[0][0], [v for _, v in run])

    # ── 2) 地形模板参数（浮点按注释标度定点化；M4 参数化的数据基础）──
    lib.write_consts(7000, [
        162,      # 7000 路缘宽
        300,      # 7001 丘陵爬升 0.3×1000
        55,       # 7002 丘陵振幅
        450,      # 7003 丘陵调制 0.45×1000
        110,      # 7004 丘陵 d 频率 0.011×10000
        16,       # 7005 丘陵 z 频率 0.0016×10000
        900,      # 7006 远丘起点
        1800,     # 7007 远丘爬升 0.18×10000
        900000,   # 7008 远丘振幅 90×10000
        50,       # 7009 远丘 d 频率 0.005×10000
        9,        # 7010 远丘 z 频率 0.0009×10000
        14000,    # 7011 沙滩下探 14×1000
        138,      # 7012 沙滩过渡宽
        250,      # 7013 cx 振幅
        1200,     # 7014 cx 波长分母
        40,       # 7015 cy 振幅
        600,      # 7016 cy 波长分母
        200,      # 7017 SEG_LEN
        500,      # 7018 NUM_SEGS
        25000,    # 7019 Z_PER_BIOME
    ])

    # ── 3) 赛道曲线表（直存 50000+，物化时从 501 点表推导 500 段）：
    #    cx[i]=sin(z/1200)·250，cy[i]=sin(z/600)·40，z=200i，i=0..500
    #    定点：毫弧度 x = z·1000//1200（编译期整数），sin×250//1000
    lib.write_n(50000, 501)
    for i in range(501):
        z = i * SEG_LEN
        PC(a, z * 1000 // 1200)
        lib.sin()
        PC(a, 250); a.emit('MULTIPLY')
        PC(a, 1000); a.emit('DIVIDE')
        lib.stream_out()
    lib.write_n(50600, 501)
    for i in range(501):
        z = i * SEG_LEN
        PC(a, z * 1000 // 600)
        lib.sin()
        PC(a, 40); a.emit('MULTIPLY')
        PC(a, 1000); a.emit('DIVIDE')
        lib.stream_out()
    lib.put_val(NUM_SEGS, 51999)

    # ── 4) 树/楼共用曲线表：z=200k+100，k=0..498；k≡1(mod 6) 无消费者 → 写 0 占位
    #    树/楼的 x,z,side,type 全部可由本表+模板索引推导，不另落 persist
    lib.write_n(55000, 499)
    for k in range(499):
        if k % 6 == 1:
            a.push_zero(); lib.stream_out()
        else:
            z = k * SEG_LEN + 100
            PC(a, z * 1000 // 1200); lib.sin()
            PC(a, 250); a.emit('MULTIPLY'); PC(a, 1000); a.emit('DIVIDE')
            lib.stream_out()
    lib.write_n(56000, 499)
    for k in range(499):
        if k % 6 == 1:
            a.push_zero(); lib.stream_out()
        else:
            z = k * SEG_LEN + 100
            PC(a, z * 1000 // 600); lib.sin()
            PC(a, 40); a.emit('MULTIPLY'); PC(a, 1000); a.emit('DIVIDE')
            lib.stream_out()

    # ── 4.5) 生物群系表（M5）：biome(i) = (i·200 // 25000) & 3，i=0..500
    #    运行时"当前生物群系"改为查此表（原 Python 侧 (z//25000)&3 已删）。
    #    501 项 = 0..499 段 + 100000 回绕端点（biome(500)=4&3=0）。
    lib.write_consts(60000, [(i * SEG_LEN // Z_PER_BIOME) & 3 for i in range(NUM_SEGS + 1)])

    # ── 4.6) P3 状态初值：圈号从 1 起（玩家一开始就在第 1 圈；过线 +1 变第 2 圈）──
    lib.put_val(1, 69104)

    # ── 4.7) HUD 文本模板（P3）：UTF-8 字节进图片（0xFF=数值占位符哨兵，0 终止）
    #    帧段只发"哪条消息 + 参数"，字形排版仍属呈现层（Python 字体渲染）
    hud_bytes = ('速度: ').encode('utf-8') + b'\xff' + ('  |  ← → 转向  ↑↓ 调速').encode('utf-8')
    lib.write_consts(HUD_KEY0, list(hud_bytes) + [0])
    # 计时板：`{}`=Piet 送来的整数，`{:02d}`=补零两位（str.format 语法，补零属排版）
    hud2_bytes = (('时间: ').encode('utf-8') + b'\xff' + b':{:02d}.{:02d}'
                  + ('  第 ').encode('utf-8') + b'\xff' + (' 圈').encode('utf-8'))
    lib.write_consts(HUD_KEY1, list(hud2_bytes) + [0])

    # ── 5) 建筑：每 2 段两侧（沙滩无建筑），组数编译期精确统计
    #    LCG 种子常驻栈上链式推进；同 z 两侧共享抽取（d,h,w,u 各抽一次）
    #    persist 布局值主序：每组 8 键 d,d,h,h,w,w,u,u（每值 DUPLICATE 双写）；
    #    x = cx表[i] ± (320+u)，z/side/type/烟囱由物化按模板推导
    lib.lcg_seed(12345)
    n_slots = 2 * sum(1 for i in range(0, NUM_SEGS, 2)
                      if ((i * SEG_LEN + 100) // Z_PER_BIOME) & 3 != 1)
    lib.write_n(65000, n_slots * 4)
    for i in range(0, NUM_SEGS, 2):
        z = i * SEG_LEN + 100
        biome = (z // Z_PER_BIOME) & 3
        if biome == 1:
            continue                      # 沙滩无建筑，不消耗抽取
        lib.lcg_draw(31, 30)              # d
        a.emit('DUPLICATE'); lib.stream_out(); lib.stream_out()
        if biome == 3:
            # 公园：keep = NOT(ku>2)（≈30% 保留），w/h 乘 keep，退化槽不可见。
            # keep 走 persist[69002] 暂存——lcg_draw 要求种子在栈顶，keep 不能驻留栈上
            lib.lcg_draw(10)                  # ku → [seed, ku]
            PC(a, 2); a.emit('GREATER'); a.emit('NOT')   # keep
            lib.put(69002)                    # 暂存 keep → [seed]
            lib.lcg_draw(31, 20)              # h0 → [seed, h0]
            lib.peek(69002)                   # → [seed, h0, keep]
            a.emit('MULTIPLY')                # h = h0·keep
            a.emit('DUPLICATE'); lib.stream_out(); lib.stream_out()
            lib.lcg_draw(51, 30)              # w0 → [seed, w0]
            lib.peek(69002)
            a.emit('MULTIPLY')                # w = w0·keep
            a.emit('DUPLICATE'); lib.stream_out(); lib.stream_out()
        elif biome == 0:
            lib.lcg_draw(201, 100)        # h = 100+s%201
            a.emit('DUPLICATE'); lib.stream_out(); lib.stream_out()
            lib.lcg_draw(51, 30)          # w = 30+s%51
            a.emit('DUPLICATE'); lib.stream_out(); lib.stream_out()
        else:
            lib.lcg_draw(81, 40)          # h = 40+s%81
            a.emit('DUPLICATE'); lib.stream_out(); lib.stream_out()
            lib.lcg_draw(51, 30)          # w = 30+s%51
            a.emit('DUPLICATE'); lib.stream_out(); lib.stream_out()
        lib.lcg_draw(41)                  # u
        a.emit('DUPLICATE'); lib.stream_out(); lib.stream_out()
    lib.lcg_drop()                            # 丢弃残留种子，帧段栈空

    # ── 6) 初始速度 + INIT 完成标志 ──
    PC(a, 3036); a.emit('IN_N')
    lib.put(2002)
    lib.put_val(1, 69003)

    # ── P5: 载具姿态（侧倾/转向/轮自转）状态初始化（决策在图片里，Python 只读）──
    for _k in (CAR_ROLL_KEY, CAR_STEER_KEY, CAR_SPIN_KEY, CAR_PREVWX_KEY, CAR_POSE_SEED_KEY):
        lib.put_val(0, _k)

    # ============ 帧段（每帧执行） ============
    lib.frame_start()

    # ===== Piet 程序：只发高层命令 =====
    # 1. 创建窗口
    PC(a, EXT_MARKER); a.emit('OUT_N')
    PC(a, EXT_CREATE_WIN); a.emit('OUT_N')
    PC(a, SW); a.emit('OUT_N'); PC(a, SH); a.emit('OUT_N')

    # 2. 天空
    PC(a, EXT_MARKER); a.emit('OUT_N')
    PC(a, EXT_CLEAR); a.emit('OUT_N')
    PC(a, 135); a.emit('OUT_N'); PC(a, 206); a.emit('OUT_N'); PC(a, 235); a.emit('OUT_N')

    # 3. 草地
    PC(a, EXT_MARKER); a.emit('OUT_N')
    PC(a, EXT_RECT); a.emit('OUT_N')
    a.push_zero(); a.emit('OUT_N'); PC(a, 300); a.emit('OUT_N')
    PC(a, SW); a.emit('OUT_N'); PC(a, 300); a.emit('OUT_N')
    PC(a, 34); a.emit('OUT_N'); PC(a, 139); a.emit('OUT_N'); PC(a, 34); a.emit('OUT_N')

    # ===== M4: 相机姿态（必须在 draw_track 之前——渲染用的 sz 与本处同源）=====
    emit_cam_pose(a, lib)

    # ===== P4: 车辆偏航角（路面切线；同样必须在 draw_track 之前）=====
    _emit_road_yaw(a, lib)

    # ===== P5: 轮自转角（读的 sz 必须与渲染同源：早于下方 M3-A 积分覆盖 persist[2001]）=====
    _emit_car_spin(a, lib)

    # ===== P3: 本帧 HUD/仪表的"速度快照" =====
    # HUD 文本与速度表/转速盘都要显示"本帧开始时"的速度（与 draw_track 用的 sz 同一时刻）。
    # 速度更新块（A2）在本段之后，所以在它之前把 2002 发布成 69112，让文本与仪表读数**同源**。
    PC(a, 2002); a.emit('IN_N'); lib.put(69112)

    # 4. 绘制赛道（Runtime 根据 scroll_z 动态投影）
    PC(a, EXT_MARKER); a.emit('OUT_N')
    PC(a, EXT_DRAW_TRACK); a.emit('OUT_N')

    # ===== P3-1: 计时（总用时 ms；t0 惰性初始化）=====
    # 本帧 tick 由 Runtime 在 vm.run() 之前填入、整帧恒定，故此处算与帧末算等价。
    # 圈号则在下面的 M3-A 里推进 → HUD 读到的圈号是"本帧开始时"的圈号，
    # 与 EXT_DRAW_TRACK 用的 sz 同源：**渲染出的每一帧里，世界位置与圈号自洽**。
    emit_timing(a, lib)

    # ===== P3: HUD（位置/颜色/消息/参数全由 Piet 决定；文本模板在 persist[61000+]）=====
    # 速度取 69112（Piet 在 A2 更新速度之前发布的"本帧开始时"快照）→ 与速度表/转速盘同源
    PC(a, EXT_MARKER); a.emit('OUT_N')
    PC(a, EXT_HUD); a.emit('OUT_N')
    PC(a, 10); a.emit('OUT_N')            # x
    PC(a, 10); a.emit('OUT_N')            # y
    PC(a, 24); a.emit('OUT_N')            # size
    PC(a, 255); a.emit('OUT_N')           # r
    PC(a, 255); a.emit('OUT_N')           # g
    PC(a, 255); a.emit('OUT_N')           # b
    PC(a, 1); a.emit('OUT_N')             # msg_id = 1
    PC(a, 1); a.emit('OUT_N')             # nargs = 1
    lib.peek(69112)                       # → 压入"本帧开始时的速度"快照
    a.emit('OUT_N')                       # arg0 = speed（与仪表同源）

    # ===== P3-1: 计时板（时间/圈数）=====
    # 时间三个分量由 Piet 分解（DIVIDE/MOD 属算术，非排版；模板里的 {:02d} 只是补零）
    PC(a, EXT_MARKER); a.emit('OUT_N')
    PC(a, EXT_HUD); a.emit('OUT_N')
    PC(a, 520); a.emit('OUT_N')           # x（右上角）
    PC(a, 10); a.emit('OUT_N')            # y
    PC(a, 24); a.emit('OUT_N')            # size
    PC(a, 255); a.emit('OUT_N')           # r
    PC(a, 255); a.emit('OUT_N')           # g
    PC(a, 255); a.emit('OUT_N')           # b
    PC(a, 2); a.emit('OUT_N')             # msg_id = 2
    PC(a, 4); a.emit('OUT_N')             # nargs = 4
    lib.peek(69102); PC(a, 60000); a.emit('DIVIDE'); a.emit('OUT_N')       # arg0 分
    lib.peek(69102); PC(a, 1000); a.emit('DIVIDE')
    PC(a, 60); a.emit('MOD'); a.emit('OUT_N')                              # arg1 秒
    lib.peek(69102); PC(a, 10); a.emit('DIVIDE')
    PC(a, 100); a.emit('MOD'); a.emit('OUT_N')                             # arg2 百分秒
    lib.peek(69104); a.emit('OUT_N')                                       # arg3 圈数

    # ===== A2: Piet 计算速度（读取键盘 → 计算 → 存回 persist[2002]）=====
    # speed = speed + up*2 - down*2，钳位 [0, 45] 由下方 Piet 算术完成
    PC(a, 2002); a.emit('IN_N')           # → [speed]
    PC(a, 202); a.emit('IN_N')            # → [speed, up]
    a.emit('PUSH', 2); a.emit('MULTIPLY') # → [speed, up*2]
    a.emit('ADD')                          # → [speed+up*2]
    PC(a, 203); a.emit('IN_N')            # → [speed+up*2, down]
    a.emit('PUSH', 2); a.emit('MULTIPLY') # → [speed+up*2, down*2]
    a.emit('SUBTRACT')                     # → [new_speed]
    # ── Piet 钳位下限：max(0, s) = s * (s > 0) ──
    a.emit('DUPLICATE')                    # → [s, s]
    PC(a, 1); PC(a, 1); a.emit('SUBTRACT') # → [s, s, 0]  （真 0：1-1）
    a.emit('GREATER')                      # → [s, s>0]
    a.emit('MULTIPLY')                     # → [max(0, s)]
    # ── Piet 钳位上限：min(45, t) = t - (t-45) * ((t-45) > 0) ──
    a.emit('DUPLICATE')                    # → [t, t]
    PC(a, 45); a.emit('SUBTRACT')          # → [t, t-45]
    a.emit('DUPLICATE')                    # → [t, t-45, t-45]
    PC(a, 1); PC(a, 1); a.emit('SUBTRACT') # → [t, t-45, 0]
    a.emit('GREATER')                      # → [t, t-45, (t-45)>0]
    a.emit('MULTIPLY')                     # → [t, (t-45)*f]
    a.emit('SUBTRACT')                     # → [min(45, t)]
    # 存回 persist[2002]
    PC(a, 2002); a.emit('PUSH', 30); PC(a, EXT_MARKER)
    a.emit('OUT_N'); a.emit('OUT_N'); a.emit('OUT_N'); a.emit('OUT_N')

    # ===== Piet 自己计算玩家位置（读取键盘 → 计算 → 存回）=====
    # new_px = px + right*15 - left*15
    PC(a, 2000); a.emit('IN_N')           # → [px]
    PC(a, 201); a.emit('IN_N')            # → [px, rk]
    a.emit('PUSH', 15); a.emit('MULTIPLY') # → [px, rd]
    a.emit('ADD')                          # → [px+rd]
    PC(a, 200); a.emit('IN_N')            # → [px+rd, lk]
    a.emit('PUSH', 15); a.emit('MULTIPLY') # → [px+rd, ld]
    a.emit('SUBTRACT')                     # → [new_px]
    # ── Piet 钳位下限：max(50, x) = x - (x-50) * NOT((x-50) > 0) ──
    a.emit('DUPLICATE')                    # → [x, x]
    PC(a, 50); a.emit('SUBTRACT')          # → [x, x-50]
    a.emit('DUPLICATE')                    # → [x, x-50, x-50]
    PC(a, 1); PC(a, 1); a.emit('SUBTRACT') # → [x, x-50, 0]
    a.emit('GREATER')                      # → [x, x-50, (x-50)>0]
    a.emit('NOT')                          # → [x, x-50, 1-f]
    a.emit('MULTIPLY')                     # → [x, (x-50)*(1-f)]
    a.emit('SUBTRACT')                     # → [max(50, x)]
    # ── Piet 钳位上限：min(750, y) = y - (y-750) * ((y-750) > 0) ──
    a.emit('DUPLICATE')                    # → [y, y]
    PC(a, 750); a.emit('SUBTRACT')         # → [y, y-750]
    a.emit('DUPLICATE')                    # → [y, y-750, y-750]
    PC(a, 1); PC(a, 1); a.emit('SUBTRACT') # → [y, y-750, 0]
    a.emit('GREATER')                      # → [y, y-750, (y-750)>0]
    a.emit('MULTIPLY')                     # → [y, (y-750)*f]
    a.emit('SUBTRACT')                     # → [min(750, y)]
    PC(a, 2000); a.emit('PUSH', 30); PC(a, EXT_MARKER)
    a.emit('OUT_N'); a.emit('OUT_N'); a.emit('OUT_N'); a.emit('OUT_N')

    # ===== M3-A: 前向积分 + 100000 回绕（规则全在 Piet）=====
    # t = scroll + speed；scroll' = t - 100000·((t-99999) > 0)
    # speed ≤ 45 保证单帧最多回绕一次
    PC(a, 2001); a.emit('IN_N')            # [z]
    PC(a, 2002); a.emit('IN_N')            # [z, s]
    a.emit('ADD')                          # [t]
    a.emit('DUPLICATE')                    # [t, t]
    PC(a, 99999); a.emit('SUBTRACT')       # [t, t-99999]
    a.push_zero(); a.emit('GREATER')       # [t, flag]（GREATER 吃掉 u 与 0）
    a.emit('DUPLICATE')                    # [t, flag, flag]
    lib.put(69110)                         # P3 过线检测：本帧是否跨过 100000 → [t, flag]
    PC(a, 100000); a.emit('MULTIPLY')      # [t, flag·100000]
    a.emit('SUBTRACT')                     # [z']
    lib.put(2001)                          # persist[2001] = z'

    # ===== P3: 圈数（过线即 +1；不变量：单帧至多回绕一次，speed ≤ 45）=====
    lib.peek(69104); lib.peek(69110)       # [lap, flag]
    a.emit('ADD')
    lib.put(69104)                         # persist[69104] = lap

    # ===== M3-B: car_wx ＝ road_cx(sz) + (px-400)·220/fov（Piet 定点）=====
    # road_cx = sin(sz/1200)·250，与 M2 赛道表同源（定点偏差 ≤2）
    PC(a, 2001); a.emit('IN_N')            # [sz]
    a.emit('DUPLICATE')                    # [sz, sz]
    PC(a, 1000); a.emit('MULTIPLY')        # [sz, sz·1000]
    PC(a, 1200); a.emit('DIVIDE')          # [sz, sz·1000//1200]
    lib.sin()                              # [sz, 1000·sin]
    PC(a, 250); a.emit('MULTIPLY')
    PC(a, 1000); a.emit('DIVIDE')          # [sz, road_cx]
    PC(a, 2000); a.emit('IN_N')            # [sz, road_cx, px]
    PC(a, 400); a.emit('SUBTRACT')         # [sz, road_cx, px-400]
    PC(a, 220); a.emit('MULTIPLY')         # [sz, road_cx, (px-400)·220]
    PC(a, 3038); a.emit('IN_N')            # [.., fov]
    a.emit('DIVIDE')                       # [sz, road_cx, 偏移]
    a.emit('ADD')                          # [sz, car_wx]
    a.emit('DUPLICATE')                    # [sz, car_wx, car_wx]
    lib.put(69010)                         # persist[69010] = car_wx
    a.emit('POP')                          # [sz]

    # ===== P5: 载具姿态（侧倾/转向）—— 必须在 M3-B 写 69010 之后（本帧 car_wx 才可见）=====
    emit_car_pose(a, lib)

    # ===== M3-C: 碰撞循环（窗口 6 格 × 两侧 = 12 次判定，编译期展开）=====
    #   窗口 a0 = clamp((sz-100)//600, 0, 161)：覆盖 twz∈[0,3000] 的全部树格
    #   pid = Π NOT(cond)；结束时 collision = NOT(pid)
    #   cond = rng · (radius·twz)² > (d·fov)²  ，d = wx - car_wx
    #   rng  = (twz ≥ 0) · (twz ≤ 3000)
    PC(a, 100); a.emit('SUBTRACT')         # [sz-100]
    PC(a, 600); a.emit('DIVIDE')           # [u]
    a.emit('DUPLICATE'); a.push_zero(); a.emit('GREATER')
    a.emit('MULTIPLY')                     # [max(0,u)]
    a.emit('DUPLICATE'); PC(a, 161); a.emit('SUBTRACT')
    a.emit('DUPLICATE'); a.push_zero(); a.emit('GREATER')
    a.emit('MULTIPLY')                     # [s, (s-161)>0]
    a.emit('SUBTRACT')                     # [min(161, max(0,u))] = a0
    lib.put(69014)                         # persist[69014] = a0

    PC(a, 1)                               # [pid = 1]
    for _j in range(6):
        lib.peek(69014)                    # [pid, a0]
        if _j:
            PC(a, _j); a.emit('ADD')       # [pid, a]
        # twz = (600a + 100) - sz → 暂存
        a.emit('DUPLICATE'); PC(a, 600); a.emit('MULTIPLY')
        PC(a, 100); a.emit('ADD')          # [pid, a, wz]
        PC(a, 2001); a.emit('IN_N'); a.emit('SUBTRACT')   # [pid, a, twz]
        lib.put(69016)                                     # [pid, a]
        # cx = 树表[55000 + 3a] → wxR = cx+180, wxL = cx-180
        #   注意：`PC(a,3); MULTIPLY` 已消耗 a，故 put(69013) 后栈即 [pid]，无需再 POP
        PC(a, 3); a.emit('MULTIPLY'); PC(a, 55000); a.emit('ADD')
        a.emit('IN_N')                                     # [pid, cx]
        a.emit('DUPLICATE'); PC(a, 180); a.emit('ADD'); lib.put(69012)
        PC(a, 180); a.emit('SUBTRACT'); lib.put(69013)      # [pid]
        emit_hit(a, lib, 69012)
        emit_hit(a, lib, 69013)
    a.emit('NOT'); lib.put(2100)           # collision = NOT(pid) → Piet 最终决定权

    # 前进完成：Runtime 从 persist[2001] 同步 scroll_z（渲染用）
    PC(a, EXT_MARKER); a.emit('OUT_N')
    PC(a, EXT_HANDLE_INPUT); a.emit('OUT_N')

    # 绘制赛车（从 persist[2100] 读取碰撞标志）
    PC(a, EXT_MARKER); a.emit('OUT_N')
    PC(a, EXT_DRAW_PLAYER); a.emit('OUT_N')

    # 刷新
    PC(a, EXT_MARKER); a.emit('OUT_N')
    PC(a, EXT_SWAP); a.emit('OUT_N')

    return a.build_image(codel_size, layout='serpentine', canvas=(1803, 1803)), [], [], []


def save_program_image(img, filename):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    img.save(path)
    print(f"[生成器] {filename}  {img.size[0]}x{img.size[1]}")


# 生成器布局用的非 Piet 标记色：实测 game_core.png 中唯一的"非 Piet 且非白"颜色。
_CODE_EXTRA_COLORS = [(74, 110, 112)]


def _code_pixel_mask(img):
    """代码像素掩码（值为 255 = 是代码）：命中 Piet 18 指令色或布局标记色的像素。
    这些像素是 Piet 程序的"指令本体"，绝不允许被当作绘画搬运。"""
    base = img.convert('RGB')
    blank = Image.new('RGB', base.size, (0, 0, 0))
    union = None
    for col in list(PIET_COLORS.keys()) + _CODE_EXTRA_COLORS:
        blank.paste(col, (0, 0, base.size[0], base.size[1]))
        m = ImageChops.difference(base, blank).convert('L').point(
            lambda p: 255 if p == 0 else 0)
        union = m if union is None else ImageChops.lighter(union, m)
    return union


def merge_previous_artwork(new_img, core_path):
    """重新生成程序图时，把上一版图里"创作区"的绘画合并回来（画作留存）。
    判定（三重，缺一不可）：
      1. 旧图该像素非纯白     —— 该处有画
      2. 旧图该像素不是代码色 —— 是用户画的，绝不会把旧代码当画搬入（布局变化也安全）
      3. 新版该处为纯白       —— 新版在这里没有代码
    因此重新生成既不丢用户的画，也绝不影响 Piet 代码逻辑。"""
    if not os.path.exists(core_path):
        return new_img
    try:
        old = Image.open(core_path).convert('RGB')
    except Exception:
        return new_img
    if old.size != new_img.size:
        return new_img
    new_img = new_img.convert('RGB')
    old_ink = old.convert('L').point(lambda p: 255 if p < 250 else 0)
    not_code = ImageChops.invert(_code_pixel_mask(old))
    new_white = new_img.convert('L').point(lambda p: 255 if p >= 250 else 0)
    mask = ImageChops.multiply(ImageChops.multiply(old_ink, not_code), new_white)
    new_img.paste(old, (0, 0), mask)
    print("[生成器] 已保留上一版创作区绘画（代码像素已按色锁定，不参与搬运）")
    return new_img


# ====================================================================
#  入口
# ====================================================================
if __name__ == '__main__':
    HERE = os.path.dirname(os.path.abspath(__file__))
    core_png = os.path.join(HERE, 'game_core.png')

    print("[启动] 生成 game_core.png (Piet 决策版)...")
    img, track_data, tree_positions, building_data = build_game_core(1)
    img = merge_previous_artwork(img, core_png)   # 保留上一版创作区绘画，重新生成不丢画
    save_program_image(img, 'game_core.png')

    print(f"[启动] 赛道段数: {len(track_data)}  树木: {len(tree_positions)}  建筑: {len(building_data)}")
    print("[启动] 启动 PietRuntime（←→移动  ↑加速  ↓减速）...")
    rt = PietRuntime(core_png, codel_size=1,
                     track_data=track_data, tree_positions=tree_positions,
                     building_data=building_data)
    rt.run()