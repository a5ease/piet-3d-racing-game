# 迁移计划：代码全部搬进 Piet + 固定画框 → 开工 P3

> **来源**：原文件 `D:\实验\.zcode\plans\plan-sess_fb6155b9-606f-4210-8d4b-ca2991bc722c.md`
> （ZCode 会话产出，2026-10-02 03:37）。2026-10-02 14:00 清理时转入项目内保存，正文未改动。
>
> **与实况的差异**（核对于 2026-10-02）：
> - M0–M5 已完成；M4 中的**侧倾/转向/轮自转**经判定属呈现层，**未迁移**（见 `M5_AUDIT.md` §6④）。
> - P3 **已于 2026-10-02 完成**：决策层（计时/圈数/过线检测，persist 69100/69102/69104/69110）与呈现层（速度表/转速盘/计时板/小地图，`emit_hud_gauges`）均已落地；文字已改纹理渲染（顺带修掉 `glDrawPixels` 白条）。门槛 `verify_p3` / `verify_p3b` + 烟雾 `smoke_p3` / `smoke_p3b`。
>   （此前一轮只完成"HUD 决策与文本模板进 Piet"。）
> - M2 的 persist 键位与计划里列的（6100/6200/6300）不同，实际用 69010/69020-69038（见代码注释键位表）。

总原则：Python 只保留 ①PietInterpreter（VM）②PietAssembler+新汇编标准库（编译器）③窗口/键盘/翻转（I/O）④GPU 光栅/深度/纹理上传（显示设备）⑤固定渲染模板（无游戏决策，数字全部来自 persist=图片写入）。已拍板：方案甲（数据 Piet 化、模板留 Python）、相机/姿态全 Piet 计算、LCG 现场计算布局、地形模板参数归 Piet。

## M0 编译器蛇形布局（画框固定）
- `PietAssembler.build_image` 增加蛇形模式：行宽=画框宽（默认保持 1803），偶数行左→右、奇数行右→左，行尾用黑墙补齐到画布边缘保证转弯几何确定，行间 2px 白道（防上下行同色块合并），代码区外全白（绘画区）；代码行数超出画框高度时编译期报错。
- 门槛测试：①`verify_decoration.py` 指令序列完全一致；②新增 `verify_layout.py` 压力用例（超长序列跨行、PUSH 块不跨行、行尾转弯零额外指令）；③游戏运行截图与旧版一致。

## M1 汇编标准库（新文件 `piet_asm.py`）
- 从 racing_game.py 拆出 PietAssembler，新增宏库：**定点 sin**（Taylor 3 项、scale=1000、mod 2π+象限折叠，全整数 DIVIDE/MOD）、**LCG 整数伪随机**（seed=(seed·1103515245+12345) mod 2³¹）、**计数循环宏**、**条件分支宏**（GREATER+SWITCH+白道几何路由）、PUT/PEEK 便捷宏。
- 门槛测试：`verify_piet_math.py`——Piet 算的 sin 与 math.sin 对比（误差≤1%）、LCG 序列与 Python 参考一致、循环/分支宏行为正确。

## M2 世界生成搬进 Piet（INIT 段，首帧执行一次）
- Piet 程序头加 INIT 守卫分支（persist[69003]=0 → 执行 INIT 并置 1；=1 → 白道几何直达帧段），INIT 内容全部由 M1 宏生成：
  - ~120 个 game_params 常量 PUT（数值进图片）
  - 赛道 500 段 → persist[50000+]（7 值/段格式不变）
  - 树（每 3 段两侧）→ persist[60000+i·3]，建筑（LCG 布局）→ persist[65000+i·7]
  - 地形模板参数（路缘 162/爬升 0.3/振幅 55/频率等 ~12 个）→ persist[7000+]
- Python 侧：`build_game_core` 的数据生成退役（保留汇编调用）；PietRuntime 首帧检测 INIT 完成后从 persist 物化 `track_data/tree_positions/buildings`（纯读数，GL 与 2D 两条渲染路径共用）；**persist 键位表写入代码注释**（2000 玩家x/2001 scroll/2002 速度/2100 碰撞、50000+ 段、60000+ 树、65000+ 楼、69000+ 元信息、6100+ 相机、6200+ 姿态、7000+ 地形参数）。
- 门槛测试：persist dump 结构断言（段数/树数/楼数/biome 划分）+ 与 float 版数值偏差 ≤2 + 4 主题区截图；INIT 只跑一次断言（69003 置位后帧步数回落）。

## M3 规则搬进 Piet
- 前进积分+100000 回卷（2001）由 Piet 计算，`handle_input` EXT 处理器退化为空壳（协议不动）。
- 碰撞判定由 Piet 循环完成：树表按 z 有序（生成即有序），Piet 用算术定位 z 窗口（约 20 棵）逐个整数距离判定 → persist[2100]；`check_collision` 处理器退化为空壳。
- 门槛测试：VM 每帧步数基准（目标 ≤6ms，保 60fps）；构造场景的碰撞置位时机与旧版一致；`verify_gl.py` 双模式回归。

## M4 相机与车辆姿态搬进 Piet
- Piet 每帧计算并 PUT：相机 eye/look（sin 宏，persist 6100-6111）、车世界坐标（6300-6302）、路切线偏航（小角度近似 yaw≈Δcx/80）、侧倾/转向（横移差分+钳位，persist 6200-6210）、轮自转。
- Python 渲染只读这些地址；`_road_cx/_road_cy/_draw_track_gl` 里的相机/姿态公式、`_draw_car3d_gl` 的动力学全部退役（地形模板按拍板执行参数化公式）。
- 门槛测试：orbit 8 角度 + `PIET_DEMO_STEER` 截图与迁移前一致（±容差）；`_road_cx` 等退役函数删除后全套回归仍绿。

## M5 全面回归 + 决策审计
- verify_gl 双模式、4 主题区、2D 回退、帧率 ≥55、INIT 一次性断言、M0-M4 所有门槛重跑。
- `verify_decoration.py` 升级为“Piet 决策审计”：dump 每帧全部命令与 persist 写入，证明世界数据/规则/相机/姿态全部出自图片。

## P3 完整 HUD（M5 通过后立即开工）
- Piet 侧：计时（IN_N 1000 ticks 差分→persist）、圈数（检测 2001 回卷 +1）、过线检测；HUD 数据 persist 区。
- 渲染模板：速度表/转速盘/计时板/小地图；文字改纹理渲染（顺带修 glDrawPixels 白条怪癖）。
- 验证：时间/圈数逻辑单测 + HUD 各主题区截图 + 全套回归。

## 已知变化与风险（已知情/接受）
- LCG ≠ Python random：建筑/树布局会变（赛道形状、玩法、规则不变）。
- 定点 sin 误差 ≤1%：赛道/相机与旧版有 ±2 世界单位偏差，视觉不可辨；碰撞窗口用保守边界补偿。
- INIT 首帧一次性开销估计 0.3-1s（循环宏控制），门槛 ≤2s，超了优化宏步数。
- 图片按蛇形布局长到约 6-12 行代码厚度，画框固定 1803×1803 绰绰有余；PietInterpreter 块分析只在加载时跑一次，不受影响。