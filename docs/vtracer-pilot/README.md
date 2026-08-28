# vtracer 微资产描摹试点记录

对应 `docs/ILLUSTRATOR_VECTOR_AUTHORING_PLAN.md` 的 Phase 0(手动试点 gate)。本目录为该
计划的试点证据:零生产代码改动,全部产物由免费开源栈产出。

## 1. 方法

- 输入:`examples/reference-only/01-modular-agent-reference-only/reference.png`
  (SHA-256 `792a16d4…38a340`,见该案 `assets.json`)的两个授权微资产紧边界裁剪:
  - `atomic:observation`,bbox [42,290,106,86](**写实照片**:台灯/机械臂/桌面);
  - `atomic:environment-globe`,bbox [1188,533,80,81](**平面插画**:地球+轨道线)。
- 描摹:vtracer(PyPI 0.6.15,核心 0.6.12;彩色模式、hierarchical=stacked、
  color_precision=6、path_precision=3,其余默认),分 spline / polygon 两模式各跑一次。
- 渲染对照:Edge headless 按 SVG 原始尺寸截图,与原裁剪逐像素对齐;
  SSIM 用 scikit-image 在原图像素尺寸上计算(与项目既有门禁同一指标族)。

## 2. 数据

| 资产 | 模式 | SSIM(对原裁剪) | 文件大小 | path 数 | 颜色数 |
|---|---|---:|---:|---:|---:|
| environment-globe(插画) | spline | 0.8146 | 27 285 B | 63 | 61 |
| environment-globe(插画) | polygon | 0.8180 | 8 955 B | 63 | 61 |
| observation(照片) | spline | 0.8525 | 30 987 B | 97 | 94 |
| observation(照片) | polygon | 0.8577 | 10 803 B | 97 | 94 |

合同子集静态检查:四个 SVG 均为纯 `<path>` 堆叠,无 `<image>`、无 mask、无渐变、
无文字转路径、无 mesh;`width/height` 等于原图像素尺寸。唯一的合同出入:vtracer
输出不带 `viewBox`(接入时由摄取层确定性补齐,属机械变换)。

视觉结论(见 `*-compare.png`,左=原裁剪,中=spline,右=polygon,4× 放大):

- **environment-globe(平面插画)**:描摹形态、配色、轨道线关系完整,展示尺寸
  (80×81)下与原图几乎不可区分;放大后可见色块边界略硬(抗锯齿被离散化)。
- **observation(写实照片)**:构图可辨,但渐变与纹理被色块化,肉眼可辨的保真度
  损失。该案 `assets.json` 对本资产的 `raster_reason` 判断(照片不可原生重建)成立,
  描摹不能改变这一点——**照片类资产留在位图层**。

## 3. 结论与建议

1. **vtracer 适合作为平面插画类微资产的默认描摹引擎**(globe 型):零成本、确定性、
   无前台切换、输出天然落在合同子集内。建议进入 Phase 1 接入评估,适用面限定为
   平面/色块类创意微资产。
2. **照片类微资产维持 atomic-raster 位图层**,不走描摹(与资产合同既有判断一致)。
3. **§8 门禁阈值需按本数据校准**:表现良好的插画类描摹在源像素尺寸下 SSIM 也仅
   0.81–0.82,§8 的 0.90 初始建议值不可达。建议方向:像素指标改为渲染尺寸下评估,
   或将 SSIM 底线校准到 0.80 档并以结构/视觉核对补足(与 StarVector「像素指标无法
   刻画矢量质量」的结论一致)。正式阈值在 Phase 1/2 用更多样本 freeze 冻结。
4. spline 与 polygon 两模式在 SSIM 上无实质差异;polygon 文件小约 3 倍。接入时按
   资产类型选模式(插画类默认 spline 的平滑边界,或按门禁结果定),不必双跑。
5. 付费对照臂(Adobe Illustrator Image Trace)未执行:本机未安装 Illustrator。
   在既有数据下,该臂对「插画类微资产描摹」无必要性论证;如未来本机具备条件,可
   按 §5.3 候选条款补测。

## 4. 产物清单与 provenance

| 文件 | 说明 |
|---|---|
| `observation.png` / `environment-globe.png` | 原图紧边界裁剪(来源见 §1,授权与权利状态沿用该案 `assets.json` 记录) |
| `*-vtracer-spline.svg` / `*-vtracer-polygon.svg` | vtracer 描摹输出(原样留档) |
| `*-compare.png` | 原裁剪 / spline / polygon 三方 4× 对照图 |

引擎:vtracer PyPI 0.6.15(核心 0.6.12);渲染:Microsoft Edge headless;
指标:scikit-image SSIM(data_range=255,channel_axis=2)。
