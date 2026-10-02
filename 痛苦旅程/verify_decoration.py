# -*- coding: utf-8 -*-
"""verify_decoration.py —— ①构建确定性 + ②Piet 决策审计

① 构建确定性
   `game_core.png`（装饰后）与 `game_core_backup.png`（装饰前）被解释器解析出的
   指令序列必须逐条相同 —— 否则装饰破坏了逻辑。

② Piet 决策审计（MIGRATION_PLAN 里 M5 承诺的那件事）
   把一帧拆成两个阶段，检查"游戏状态是谁写的"：

     【Piet 执行阶段】 vm.run()            —— 允许（且必须）在这里写 persist
     【命令执行阶段】 _exec_cmd(每条命令)  —— 必须对 persist **零写入**

   两阶段之间 persist 出现任何差异 ⇒ Python 侧存在运行时决策残留。
   再对照键白名单：帧段写出的键必须都在契约内，且全部游戏状态键都必须出现在
   Piet 的写入集合里（不能有任何游戏状态由 Python 补齐）。

   审计附带的防空真：注入受控键盘输入，保证状态真的在变（否则"零写入"可能
   因为整帧什么都不做而恒真）。

历史：本脚本曾只 `print('完全一致: ...')` 而不设退出码 —— 批量回归里永远 exit=0，
      `完全一致: False` 也照样"全绿"（2026-10-02 发现，是个假门禁）。现已修正。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_VIDEO_WINDOW_POS', '2000,2000')
os.environ.setdefault('PIET_FORCE_2D', '1')       # 审计只看 persist，与渲染通道无关

from racing_game import PietInterpreter, PietRuntime

MAX_STEPS = 4_000_000        # 与 PietRuntime 一致：INIT 段约 60-70 万步
AUDIT_FRAMES = 12

# ── 帧段允许 Piet 写的键（persist 契约）──
FRAME_KEYS = {
    2000, 2001, 2002, 2100,                              # 玩家x / scroll / 速度 / 碰撞
    69010, 69012, 69013, 69014, 69016,                   # car_wx / 树wx(右左) / 窗口a0 / twz
    69020, 69022, 69024, 69026, 69028, 69030, 69032,     # M4 相机 eye/look + car_wy
    69038,                                               # P4 road_yaw
    6200, 6202, 6204, 6206, 6208,                        # P5 载具姿态 roll/steer/spin + 滤波状态
    69100, 69102, 69104, 69110, 69112,                   # P3 计时/圈数/过线/速度快照
}

# ── 每帧都必须由 Piet 决定的游戏状态键（缺一即说明有状态由 Python 补齐）──
GAME_STATE_KEYS = {
    2000, 2001, 2002, 2100,
    69010, 69020, 69022, 69024, 69026, 69028, 69030, 69032, 69038,
    6200, 6202, 6204, 6206, 6208,
    69100, 69102, 69104, 69110, 69112,
}

# ── INIT 段必须写入的世界数据键（抽样：证明世界数据出自图片）──
INIT_SAMPLE_KEYS = {4100, 4106, 51999, 50000, 50600, 55000, 60000, 60500, 65000, 69003}

FAILS = []


def check(name, cond, detail=''):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f'  ({detail})' if detail else ''),
          flush=True)
    if not cond:
        FAILS.append(name)


def get_cmds(path):
    vm = PietInterpreter(path, codel_size=1, halt_on_backward=False, max_steps=MAX_STEPS)
    vm.reset()
    return vm.run()


def audit_determinism():
    print('── ① 构建确定性 ──', flush=True)
    a = get_cmds(os.path.join(HERE, 'game_core_backup.png'))   # 装饰前
    b = get_cmds(os.path.join(HERE, 'game_core.png'))          # 装饰后
    print(f'  装饰前 指令数: {len(a)}', flush=True)
    print(f'  装饰后 指令数: {len(b)}', flush=True)
    check('两次构建的指令序列逐条相同', a == b,
          '' if a == b else f'长度 {len(a)} vs {len(b)}')
    if a != b:
        for i in range(max(len(a), len(b))):
            va = a[i] if i < len(a) else '<缺失>'
            vb = b[i] if i < len(b) else '<缺失>'
            if va != vb:
                print(f'  第 {i} 条不同: 前={va}  后={vb}', flush=True)
                break
    return a


def audit_decisions():
    print('── ② Piet 决策审计（Piet 执行阶段 vs 命令执行阶段）──', flush=True)
    core = os.path.join(HERE, 'game_core.png')
    rt = PietRuntime(core, codel_size=1, track_data=[], tree_positions=[], building_data=[])
    p = rt.vm.runtime['persist']

    frames = []
    for f in range(AUDIT_FRAMES):
        # 受控输入：中段加速、后段左转 —— 保证状态真的在变（防空真）
        rt.vm.runtime['ticks'] = 1000 + f * 16
        rt.vm.runtime['keys'][202] = 1 if 2 <= f <= 5 else 0
        rt.vm.runtime['keys'][200] = 1 if f >= 7 else 0

        before = dict(p)
        rt.vm.reset()
        cmds = rt.vm.run()
        after_piet = dict(p)
        for c in cmds:
            rt._exec_cmd(c)
        after_exec = dict(p)

        piet_wrote = {k for k, v in after_piet.items() if before.get(k) != v}
        exec_wrote = {k for k, v in after_exec.items() if after_piet.get(k) != v}
        frames.append({'n': f, 'cmds': cmds, 'piet': piet_wrote, 'exec': exec_wrote,
                       'steps': rt.vm.step_count_last, 'p': dict(after_exec)})

    # ── 核心断言 1：命令执行阶段对 persist 零写入 ──
    dirty = [(fr['n'], sorted(fr['exec'])) for fr in frames if fr['exec']]
    check('命令执行阶段对 persist 零写入（每帧）', not dirty,
          f'{len(dirty)}/{len(frames)} 帧有写入' + (f'  例 {dirty[0]}' if dirty else ''))

    # ── 核心断言 2：帧段写出的键都在契约内 ──
    frame_written = set()
    for fr in frames[1:]:                     # 帧 1 含 INIT，故只看帧 ≥2
        frame_written |= fr['piet']
    stray = sorted(frame_written - FRAME_KEYS)
    check('帧段写出的键全部在 persist 契约内', not stray, f'越界键 {stray}')

    # ── 核心断言 3：全部游戏状态键都由 Piet 写出 ──
    all_written = set()
    for fr in frames:
        all_written |= fr['piet']
    missing = sorted(GAME_STATE_KEYS - all_written)
    check('每一帧的游戏状态键都由 Piet 写出（无 Python 补齐）', not missing,
          f'未被 Piet 写 {missing}')

    # ── 核心断言 4：世界数据在 INIT 段由 Piet 写入 ──
    init_wrote = frames[0]['piet']
    missing_init = sorted(INIT_SAMPLE_KEYS - init_wrote)
    print(f'  帧1（含 INIT）Piet 写入 {len(init_wrote)} 个键；帧≥2 每帧约 '
          f'{sum(len(fr["piet"]) for fr in frames[1:]) // max(1, len(frames) - 1)} 个键', flush=True)
    check('INIT 段由 Piet 写入世界数据（抽样键齐全）', not missing_init,
          f'缺 {missing_init}')

    # ── 防空真：状态确实在变 ──
    zs = [fr['p'][2001] for fr in frames]
    sps = [fr['p'][2002] for fr in frames]
    check('审计非空真：scroll 单调递增', all(b > a for a, b in zip(zs, zs[1:])), f'{zs}')
    check('审计非空真：速度受键盘控制确实变化', len(set(sps)) > 1, f'{sps}')
    check('审计非空真：每帧都有 Piet 写入', all(fr['piet'] for fr in frames[1:]),
          f'空帧 {[fr["n"] for fr in frames[1:] if not fr["piet"]]}')

    # ── 帧段命令流非空 & 步数在预算内 ──
    # 步数预算针对**帧段**（帧 ≥2）；帧 1 含 INIT（约 45 万步）不计入。
    empty = [fr['n'] for fr in frames if not fr['cmds']]
    check('每帧都产出绘制命令', not empty, f'空帧 {empty}')
    seg = [fr['steps'] for fr in frames[1:]]
    worst = max(seg)
    print(f'  帧1（含 INIT）步数={frames[0]["steps"]}（一次性，不计预算）', flush=True)
    print(f'  帧段步数 max={worst} / min={min(seg)}（预算 8000）', flush=True)
    check('帧段单帧步数在预算内', worst <= 8000, f'max={worst}')


def main():
    print('== verify_decoration：构建确定性 + Piet 决策审计 ==', flush=True)
    for f in ('game_core.png', 'game_core_backup.png'):
        if not os.path.exists(os.path.join(HERE, f)):
            check(f'{f} 存在', False, '缺失 —— 先跑一次构建')
            print(f'\n== 结果: {len(FAILS)} 项失败 ==', flush=True)
            sys.exit(1)

    audit_determinism()
    audit_decisions()

    print()
    if FAILS:
        print(f'== 结果: {len(FAILS)} 项失败 ==', flush=True)
        for n in FAILS:
            print(f'   - {n}', flush=True)
        sys.exit(1)
    print('== 结果: 全部通过 ==', flush=True)
    sys.exit(0)


if __name__ == '__main__':
    main()
