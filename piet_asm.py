"""Piet 汇编标准库（M1）—— 全部宏只发射 Piet 指令序列，零运行时决策。

哲学边界：本文件是【编译器】的一部分。所有宏把 Piet 栈机代码编译进图片，
游戏的一切数值由图片里的指令在运行时算出。宏本身在编译期不做任何
游戏数据计算（数学常数如 π 的定点近似除外——它们是"指令集的 ROM"，不是世界数据）。

定点约定（S=1000）：
  角度 x 为毫弧度（milli-rad，即 rad×1000，≥0），sin 输出为 sin(x/1000)×1000 ∈ [-1000, 1000]。
  Taylor 三阶 + 整数 Horner：
    sin/x = 1 - q/D1 + q²/D2 - q³/D3，q = x²
    D1 = 6e6, D2 = 120e12, D3 = 5040e18
  整数 Horner（乘 D1·D2·D3 消分母）：
    P = ((D1D3 - q)·q - D2D3)·q + D1D2D3，sin = x·P / (D1D2D3)
  区间归约：mod 2π → 超 π 翻号（f）→ 超 π/2 镜像（g），全部用 GREATER 算术化，无分支。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from racing_game import (PietAssembler, EXT_MARKER, EXT_WRITE_N,
                         EXT_SKIP_IF, EXT_FRAME_START)

# sin 数学常数的 persist 键（INIT 期由 Piet 写入，之后 PEEK）
SIN_2PI, SIN_PI, SIN_HPI = 4100, 4101, 4102
SIN_D23, SIN_C0, SIN_D13 = 4103, 4104, 4105
LCG_A_KEY, LCG_C_KEY, LCG_M_KEY = 4106, 4107, 4108   # LCG 常量（INIT 期写入）
INIT_FLAG_KEY = 69003         # INIT 完成标志（守卫分支用，IN_N 可读范围已扩到 70000）

D1 = 6_000_000
D2 = 120_000_000_000_000
D3 = 5_040_000_000_000_000_000_000
D23 = D2 * D3
D13 = D1 * D3
C0 = D1 * D23


class PietLib:
    """PietAssembler 宏层。用法：lib = PietLib(a)；所有宏向 a 发射指令。"""

    def __init__(self, a: PietAssembler):
        self.a = a

    # ── 基础 ──
    def roll(self, count, depth):
        """栈顶旋转：VM 弹序为 先 count（栈顶）后 depth，故先压 depth 再压 count"""
        a = self.a
        a.push(depth)
        a.push(count)
        a.emit('ROLL')

    def put(self, key):
        """消耗栈顶 value：persist[key] = value"""
        a = self.a
        a.push_compact(key)
        a.push(30)                      # EXT_PUT
        a.push_compact(EXT_MARKER)
        for _ in range(4):
            a.emit('OUT_N')

    def put_val(self, val, key):
        """常量版：persist[key] = val"""
        self.a.push_compact(val)
        self.put(key)

    def peek(self, key):
        """persist[key] → 压栈"""
        a = self.a
        a.push_compact(key)
        a.emit('IN_N')

    # ── M2: 批量顺序写（EXT_WRITE_N）──
    def write_n(self, base, count):
        """批写头：后续 count 个 OUT_N 依次写 persist[base+i] = 栈顶值
        （栈弹序与压序相反：先弹 base 后弹 count，故先压 count 再压 base）"""
        a = self.a
        a.push_compact(count)
        a.push_compact(base)
        a.push_compact(EXT_WRITE_N)
        a.push_compact(EXT_MARKER)
        for _ in range(4):
            a.emit('OUT_N')

    def stream_out(self):
        """栈顶值流入当前 WRITE_N 批（消耗栈顶）"""
        self.a.emit('OUT_N')

    def write_consts(self, base, values):
        """常量批写：persist[base+i] = values[i]"""
        self.write_n(base, len(values))
        for v in values:
            self.a.push_compact(v)
            self.stream_out()

    # ── M2: INIT 守卫跳转 ──
    def skip_if(self):
        """[flag] → 消耗 flag。flag=1 时 VM 跳到 EXT_FRAME_START 记录的帧段块。
        flag 由 Piet 算术算出（决策在 Piet），VM 机械执行跳转。"""
        a = self.a
        a.push_compact(EXT_SKIP_IF)
        a.push_compact(EXT_MARKER)
        for _ in range(3):
            a.emit('OUT_N')

    def frame_start(self):
        """帧段起始标记：首次执行时 VM 记录 (块,DP,CC) 供 skip_if 跳转"""
        a = self.a
        a.push_compact(EXT_FRAME_START)
        a.push_compact(EXT_MARKER)
        for _ in range(2):
            a.emit('OUT_N')

    # ── 定点 sin ──
    def emit_sin_constants(self):
        """把 sin 所需数学常数写入 persist（INIT 期调用一次）"""
        self.put_val(6283, SIN_2PI)
        self.put_val(3141, SIN_PI)
        self.put_val(1570, SIN_HPI)
        self.put_val(D23, SIN_D23)
        self.put_val(C0, SIN_C0)
        self.put_val(D13, SIN_D13)

    def sin(self):
        """[x 毫弧度 ≥0] → [sin(x/1000)×1000]。约 70 条指令，无分支。
        栈计划（每行已逐步仿真核对）：
          fold1: [x] → [x2]                x2 = x mod 2π
          fold2: [x2] → [f, x2']           f = x2 > π；x2' = x2 - πf
          fold3: [f, x2'] → [f, x3]        g = x2' > π/2；x3 = (1-2g)·x2' + πg
          Horner: [f, x3] → [f, sin]       P = ((D13 - q)·q - D23)·q + C0，sin = x3·P/C0
          符号:   [f, sin] → [sin·(1-2f)]
        """
        a = self.a
        R = a.emit

        # fold1
        self.peek(SIN_2PI)
        R('MOD')                                    # [x2]
        # fold2
        R('DUPLICATE')
        self.peek(SIN_PI)
        R('GREATER')                                # [x2, f]
        R('DUPLICATE')
        self.peek(SIN_PI)
        R('MULTIPLY')                               # [x2, f, πf]
        self.roll(2, 3)
        self.roll(1, 2)
        R('SUBTRACT')                               # [f, x2']   x2' = x2 - πf
        # fold3
        R('DUPLICATE')
        self.peek(SIN_HPI)
        R('GREATER')                                # [f, x2', g]
        R('DUPLICATE')
        self.peek(SIN_PI)
        R('MULTIPLY')                               # [f, x2', g, πg]
        self.roll(1, 3)                             # [f, πg, x2', g]
        R('DUPLICATE')
        R('ADD')                                    # [f, πg, x2', 2g]
        a.push(1)
        self.roll(1, 2)
        R('SUBTRACT')                               # [f, πg, x2', u]   u = 1-2g
        self.roll(1, 2)                             # [f, πg, u, x2']
        R('MULTIPLY')                               # [f, πg, u·x2']
        R('ADD')                                    # [f, x3]
        # 整数 Horner（免深杂耍：q 每级由 x3 重算）
        R('DUPLICATE')
        R('DUPLICATE')
        R('MULTIPLY')                               # [f, x3, q]        q = x3²
        self.peek(SIN_D13)
        self.roll(1, 2)
        R('SUBTRACT')                               # [f, x3, s1 = D13 - q]
        self.roll(1, 2)                             # [f, s1, x3]
        R('DUPLICATE')
        R('DUPLICATE')
        R('MULTIPLY')                               # [f, s1, x3, q]
        self.roll(2, 3)                             # [f, x3, q, s1]
        R('MULTIPLY')                               # [f, x3, q·s1]
        self.peek(SIN_D23)
        R('SUBTRACT')                               # [f, x3, s2 = q·s1 - D23]
        self.roll(1, 2)                             # [f, s2, x3]
        R('DUPLICATE')
        R('DUPLICATE')
        R('MULTIPLY')                               # [f, s2, x3, q]
        self.roll(2, 3)                             # [f, x3, q, s2]
        R('MULTIPLY')                               # [f, x3, q·s2]
        self.peek(SIN_C0)
        R('ADD')                                    # [f, x3, P]
        R('MULTIPLY')                               # [f, x3·P]
        self.peek(SIN_C0)
        R('DIVIDE')                                 # [f, sin]
        # 符号
        self.roll(1, 2)                             # [sin, f]
        R('DUPLICATE')
        a.push(2)
        R('MULTIPLY')                               # [sin, f, 2f]
        a.push(1)
        self.roll(1, 2)
        R('SUBTRACT')                               # [sin, f, u]   u = 1-2f
        self.roll(1, 3)                             # [u, sin, f]   把 f 转到栈顶再丢弃
        a.emit('POP')                               # [u, sin]
        R('MULTIPLY')                               # [sin·(1-2f)] ✓（乘 sin 不是乘 f）

    # ── LCG 整数伪随机 ──
    LCG_A = 1103515245
    LCG_C = 12345
    LCG_M = 2147483648

    def emit_lcg_constants(self):
        """LCG 常量写入 persist（INIT 期一次），抽取时 PEEK 免大数压栈"""
        self.put_val(self.LCG_A, LCG_A_KEY)
        self.put_val(self.LCG_C, LCG_C_KEY)
        self.put_val(self.LCG_M, LCG_M_KEY)

    def lcg_seed(self, seed0):
        """压入初始种子（种子常驻栈上，整条链不落 persist）"""
        self.a.push_compact(seed0)

    def lcg_drop(self):
        """INIT 末尾丢弃残留种子，保证进帧段时栈空"""
        self.a.emit('POP')

    def lcg_draw(self, n, base=0):
        """[seed] → [seed, base + seed' % n]；seed' 留栈续用。
        s' = (s·A + C) mod 2³¹，常量走 PEEK（persist），栈深度不变。"""
        a = self.a
        R = a.emit
        self.peek(LCG_A_KEY)
        R('MULTIPLY')
        self.peek(LCG_C_KEY)
        R('ADD')
        self.peek(LCG_M_KEY)
        R('MOD')
        R('DUPLICATE')                  # [seed', seed']
        a.push_compact(n)
        R('MOD')                        # [seed', seed' % n]
        if base:
            a.push_compact(base)
            R('ADD')                    # [seed', base + seed' % n]

    def lcg_step(self, seed_key):
        """persist[seed_key] = (seed·A + C) mod 2³¹，并把新种子留在栈顶"""
        a = self.a
        R = a.emit
        self.peek(seed_key)
        a.push_compact(self.LCG_A)
        R('MULTIPLY')
        a.push_compact(self.LCG_C)
        R('ADD')
        a.push_compact(self.LCG_M)
        R('MOD')
        R('DUPLICATE')
        self.put(seed_key)              # 消耗一份副本存回；栈顶仍留新种子

    def rand_below(self, n, base=0):
        """栈顶种子 → 消耗之，压入 base + seed % n"""
        a = self.a
        a.push_compact(n)
        a.emit('MOD')
        if base:
            a.push_compact(base)
            a.emit('ADD')
        else:
            a.push(base)
            a.emit('ADD')
