# 05 · PNG 与文字证据的权威边界

## 核心原则

参考 PNG 是视觉事实，不是所有科研语义的唯一真值。高保真重建必须同时避免两种错误：擅自“美化”而偏离参考，以及忠实复制 PNG 中本来就不可辨或错误的文字、公式和箭头。

“严格一比一”按维度拆分：

| 维度 | 权威 | 验收 |
|---|---|---|
| 画布、几何、布局、颜色和视觉重量 | 冻结 PNG | 以脚本测量和阈值容差为准 |
| 普通文字候选 | 本地 OCR + 原图局部视觉 + 可用原文 | 一致证据可冻结；冲突显式保留 |
| 关键文字、数字、单位 | 用户确认的 spec | 逐字零容差 |
| 拓扑、箭头方向和线型语义 | 用户确认的 spec | 端点和关系零容差 |
| 公式 | 用户提供/确认的 LaTeX 或可靠原文 | 普通 OCR 只能发现候选，不能自证公式 |

## OCR 的位置

PaddleOCR 是**辅助感知器**：它提供文字框、候选文本、置信度和方向证据，降低视觉模型漏字、臆测和错行的概率。它不是最终权威，也不决定科研含义。

- 全图与重叠分块结果一致只能提高候选优先级；经用户确认或可靠原文复核后，spec `disposition` 才可写 `CONFIRMED`。
- 不同尺度、方向或视觉读法不一致时，保留 alternatives，并标 `INCONCLUSIVE`。
- OCR 置信度只表示模型内部识别把握，不能直接转换为“语义已确认”。
- 希腊字母、上下标、分数、根号、矩阵和多行公式必须走公式分支或人工确认。

## 落地规则

1. 冻结参考绝对路径、SHA-256、像素尺寸、颜色模式和用户确认状态。
2. 在绘制前生成 source-bound raw perception manifest，为其完整候选集生成决策模板，再用用户确认/可靠原文生成 hash-bound review receipt。OCR 不能为自己签发 receipt。
3. Figure spec 只能从当前 raw manifest + `PERCEPTION_REVIEW_PASS` receipt 映射；每个文本对象记录 bbox、candidate ID、来源、置信度、disposition 与不确定性。`disposition` 只能是 `CONFIRMED/INCONCLUSIVE/UNREADABLE/NOT_TEXT`。
4. 先创建与参考同比例的空白 canvas PPTX，再做 preflight。preflight receipt 必须绑定 source、raw manifest、review receipt、spec、schema、script 及读回验证的空白 deck 哈希。
5. Drawer 只执行 frozen spec，只打开 `PASS` preflight receipt 指向的 deck，不重新看 PNG 猜字、改拓扑或改换行。
6. 参考图不得嵌入 final；只用于测量和审计，`reference_embedded=false`。
7. 当视觉保真与经确认的语义冲突时，记录 `intentional_deviation`；宁可产生局部像素差，也不能篡改正确语义。
8. 保存后读取 PPT 文本与 frozen spec 逐字比较；OCR 只用于发现渲染丢字、字体替换或错误换行，不替代对象读回。
