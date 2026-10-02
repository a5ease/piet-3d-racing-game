# M1 汇编标准库验证：定点 sin / LCG 在 Piet VM 中的数值正确性
# 方法：编译一个纯 Piet 程序（样例角度 → sin → persist；LCG 链 → persist），
#        用 PietInterpreter 跑图片，读 persist 与 Python 参考实现对比。
import math
import os
import sys

os.environ['SDL_VIDEODRIVER'] = 'dummy'
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from piet_asm import PietLib, INIT_FLAG_KEY
from racing_game import PietAssembler, PietInterpreter

FAILS = []


def check(name, cond, detail=''):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f'  ({detail})' if detail else ''), flush=True)
    if not cond:
        FAILS.append(name)


def build_math_program():
    lib = PietLib(PietAssembler())
    lib.emit_sin_constants()
    # ── sin 样例 ──
    angles = ([0, 1, 100, 785, 786, 1200, 1570, 1571, 2000, 3140, 3141, 3142,
               4000, 4712, 5000, 6282, 6283, 6284, 9425, 12566]
              + [z * 5 // 6 for z in (0, 200, 600, 1200, 6000, 12000, 25000, 49980, 75000, 99900)])
    for i, ang in enumerate(angles):
        lib.a.push_compact(ang)
        lib.sin()
        lib.put(7000 + i)
    # ── LCG 链 ──
    lib.put_val(12345, 4200)
    for i in range(24):
        lib.lcg_step(4200)
        lib.a.push_compact(51)
        lib.a.emit('MOD')
        lib.a.push(30)
        lib.a.emit('ADD')
        lib.put(7200 + i)
    return angles, lib.a


def main():
    print('== M1 汇编标准库验证 ==', flush=True)
    angles, a = build_math_program()
    img = a.build_image(layout='serpentine', canvas=(1803, 1803))
    print(f'  图片尺寸: {img.size}  指令数: {len(a.code)}', flush=True)
    img.save('_math_test.png')
    vm = PietInterpreter('_math_test.png', codel_size=1, halt_on_backward=False, max_steps=2_000_000)
    vm.reset()
    vm.run()

    # ── sin 断言 ──
    worst = 0.0
    sin_fail = 0
    for i, ang in enumerate(angles):
        got = vm.runtime['persist'].get(7000 + i)
        ref = round(1000 * math.sin(ang / 1000.0))
        err = abs(got - ref)
        worst = max(worst, err)
        if err > 6:
            sin_fail += 1
            if sin_fail <= 5:
                print(f'    sin({ang}) got={got} ref={ref} err={err}', flush=True)
    check(f'sin 数值（{len(angles)} 样例，容差 ±6/1000）', sin_fail == 0, f'最大误差 {worst}')

    # ── LCG 断言 ──
    seed = 12345
    lcg_fail = 0
    for i in range(24):
        seed = (seed * 1103515245 + 12345) % 2147483648
        got = vm.runtime['persist'].get(7200 + i)
        if got != seed % 51 + 30:
            lcg_fail += 1
            if lcg_fail <= 3:
                print(f'    LCG#{i} got={got} ref={seed % 51 + 30}', flush=True)
    check('LCG 链（24 步）', lcg_fail == 0)

    try:
        os.remove('_math_test.png')
    except OSError:
        pass

    print()
    if FAILS:
        print(f'== 结果: {len(FAILS)} 项失败 ==')
        sys.exit(1)
    print('== 结果: 全部通过 ==')


if __name__ == '__main__':
    main()
