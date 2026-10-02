"""直接运行游戏，捕获所有错误"""
import sys
import traceback
import os

# 用本文件所在目录作为项目根，避免硬编码绝对路径（可换机器运行）
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 依赖自查：缺失时给出安装提示（国内镜像）
try:
    import pygame  # noqa: F401
    from PIL import Image  # noqa: F401
except ImportError as e:
    print(f"[错误] 缺少依赖：{e.name}")
    print("请先安装依赖： pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple")
    sys.exit(1)

from racing_game import PietRuntime, build_game_core, merge_previous_artwork

core_png = os.path.join(HERE, 'game_core.png')
error_log = os.path.join(HERE, 'error_log.txt')
try:
    img, track_data, tree_positions, building_data = build_game_core(1)
    img = merge_previous_artwork(img, core_png)   # 保留上一版创作区绘画，重新生成不丢画
    img.save(core_png)   # M2: build_game_core 只返回图片，必须落盘后 PietRuntime 才能加载
    rt = PietRuntime(core_png, codel_size=1,
                     track_data=track_data, tree_positions=tree_positions,
                     building_data=building_data)
    rt.run()
except Exception as e:
    with open(error_log, 'w', encoding='utf-8') as f:
        f.write(f'错误: {e}\n')
        f.write(traceback.format_exc())
    print(f'错误: {e}', flush=True)
    traceback.print_exc()