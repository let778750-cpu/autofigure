# 04 · 期刊发表 / 数据图表规范

> 来源：`D:\科研绘图agent\scipilot-figure-skill\`（journal_specs.md / publication_checklist.md / viz_pitfalls.md / visual_review.md / chart_selection.md / plot_recipes.md / data_profiling.md + check_figure.py / visual_qa.py）。
> 本域是「数据图/图表」的期刊发表规范，与 01-03 的「示意图/架构图」互补；用于最终稿的投稿合规检查。
> **与 03 的分界**：04 管「嵌入的数据图」（matplotlib 图表：字号 pt / 线宽 pt / DPI / 色盲 / 误差交代 / 子图标签），03 管「示意图/架构」骨架（px 画布 / 模块层级 / 箭头语义）。同一张方法图内嵌 loss 曲线/概率分布/热图时，这些内嵌图按 04，外围架构按 03；两者不冲突，仅在「字号/线宽数值」上因 px-vs-pt 语境不同而看似矛盾，实际是不同对象类型。

## 1. 各期刊关键数值（速查）

| 期刊 | 单栏(in) | 双栏(in) | 字号(pt) | 字体 | 位图 DPI | 矢量 | 子图标签 |
|---|---|---|---|---|---|---|---|
| Nature 系 | 3.5 | 7.2 | 5-7 | Helvetica/Arial | 300+/线条600 | EPS/PDF | a b c |
| Science | 2.2 | 7.2 | 5-7 | Helvetica/Arial | 300+/线条600 | PDF/EPS | A B C |
| IEEE | 3.5 | 7.16 | 8-10 | Times | 600/照片300 | PDF/EPS | (a)(b)(c) |
| Elsevier | 3.54 | 7.48 | 7-9 | Helvetica/Arial | 300/线条1000 | EPS/PDF | (A)(B)(C) |
| PNAS | 3.42 | 7.0 | 6-8 | Helvetica/Times | 300/黑白600 | PDF/EPS | (A)(B)(C) |
| 中文核心 | 3.15 | 6.7 | 8-9 | 宋体+TNR | 600/照片300 | PDF(勿EPS) | 与期刊一致 |

- 中文字体优先级：`Noto Sans CJK SC > Source Han Sans SC > SimHei > Microsoft YaHei`；宋体 `Noto Serif CJK SC > Source Han Serif SC > SimSun`。
- 中文图：中文走中文字体，数字/变量/单位走 Times New Roman；中文期刊 PDF 优先于 EPS（EPS 对 TrueType 中文支持差）。

## 2. 五条硬性原则

1. **按最终尺寸出图**（figsize 直接设最终尺寸），导出后不在 Word/LaTeX/PPT 二次缩放。
2. **矢量优先**：数据图（线/柱/散点/热力/箱线）→ PDF/SVG/EPS；显微图/照片 → PNG/TIFF ≥300 DPI；**绝不用 JPEG**。
3. **色盲友好**：Okabe-Ito（`#000000 #E69F00 #56B4E9 #009E73 #F0E442 #0072B2 #D55E00 #CC79A7`）或 `colorblind` + 冗余编码（颜色+线型/marker）；避免红绿对比；灰度预览仍可分。
4. **字号最终尺寸可读**：正文标签 7-9 pt、刻度 6-8 pt、**最小 ≥ 6 pt**；字体 ≤ 2 种。
5. **误差必有交代**：SD/SEM/95%CI/IQR + n + 检验方法（t/Mann-Whitney/ANOVA）+ 多重比较校正 + 显著性符号定义（`* p<0.05, ** p<0.01, *** p<0.001`）。

## 3. 发表前检查清单（精选 50 条 → 归纳 8 类）

- **尺寸/分辨率**：尺寸偏差 < 0.1 in；DPI ≥ 300（线条 600）；PDF fonttype 42（无 Type 3）；SVG 无 base64 位图。
- **格式**：无 JPEG；数据图矢量。
- **字体**：期刊字体一致；无方框（`axes.unicode_minus=False`）。
- **配色**：Okabe-Ito/colorblind；无 rainbow/jet；连续值用 viridis/magma/RdBu_r；双向发散 + `center=0`；色阶必带 colorbar + label/单位。
- **轴/标签**：轴 label 含变量名+单位；刻度精度合理；log 轴标明；比例从 0 起（截断画断裂标记）。
- **图例/子图标签**：图例不遮数据（`frameon=False`）；类别 >5 用直接标注；子图标签格式按期刊 + 位置统一（左上）。
- **误差/统计**：凡误差棒/阴影必写误差类型 + n + 检验。
- **终检**：`check_figure.py --strict` exit 0；色盲模拟；非同行能看懂 x/y/类别。

## 4. 常见图表误区（viz_pitfalls，主动拦截）

| 误区 | 替代 |
|---|---|
| P1 均值柱掩盖分布/样本量（n<10） | 箱线+stripplot / 直接散点 |
| P2 双 Y 轴误导 | 拆子图 / 标准化 / 散点 |
| P3 饼图 / 3D 图 | 横向柱（排序）/ 堆叠柱 / 2D 热力图 |
| P4 Y 轴不当截断 | 比例从 0 起 / log / 断裂标记 |
| P5 连续色无 colorbar | 必加 colorbar + label/单位 |
| P6 分类变量连折线 | 柱/箱线/点图 |
| P7 颜色过多（>7） | 类别≤5 颜色+编码；>12 拆图 |
| P12 一图多论点 | 一图一个核心结论 |
| P13/P14 红绿对比 / rainbow | 色盲安全 / 感知均匀色图 |
| P16 缺字乱码 | setup_style(lang)+unicode_minus=False |

## 5. 图表类型选择（chart_selection）

- 分布→直方图/KDE/箱线；比较→箱线/带误差柱/小提琴；关系→散点+回归；趋势→折线+误差带；构成→堆叠柱（禁饼图）；相关→热力图。
- 样本量：n<3 直接列点；3≤n<10 stripplot（箱线慎用）；10≤n<30 箱线+stripplot；n≥30 箱线/带误差柱。
- 散点 >1000 点 alpha=0.1-0.3 或 hexbin。
- 拆图标准（任一条）：维度组合 >12 / x 分类 >8 / 图例 >6 / y 跨数量级不可 log / 想说两件事。

## 6. 视觉审阅闭环（visual_review）

绘制 → 渲染 PNG 预览 → 程序自检（`visual_qa` 确定性：缺字/越界/刻度重叠）→ AI 读图自检（图例压数据/重叠/子图对齐/配色灰度/数据完整性/跨子图一致性）→ 回改 → 通过导出。
- 程序自检阈值：文字越界容差 `clip_tol_px=2.0`；刻度标签重叠容差 `overlap_tol_px=1.0`。
- 每改一处重渲一次；**最多 3 轮**，不过 → 图型选错（回 chart_selection）或维度太多（拆图）。
