# check 报告（strict） — 01-modular-agent-reference-only

## 像素诊断（figure_lint，软信号）
- mean_abs_rgb_delta: 15.3626
- changed_pixel_ratio: 37.0966%
- top_roi: {'bbox': {'x': 1000, 'y': 480, 'w': 400, 'h': 80}, 'mean_abs_rgb_delta': 31.8008, 'loss_contribution_pct': 6.8089}
- ssim: 0.7561
- diff 图: qa/diff.png
- 对照预览: preview.png
- 关键区域 strict_pass: False（6 个关键区域）
- 区域明细: qa/regions-report.json
- 布局合同: PASS（0 项）
- 布局明细: qa/layout-audit.json

## 文本比对（SVG 文字 vs 参考图 OCR）
- SVG 侧未匹配 1 条（可能：VLM 错字 / OCR 漏识 / 粒度差异）
- OCR 侧未匹配 24 条（可能：VLM 漏画 / OCR 误识）

### SVG 侧未匹配
- zτt+2

### OCR 侧未匹配
- Task 2: Close top drawer ...
- Task-Aware Modular Joint
- task-conditioned imagination
- Task-Guided Expert Allocator
- Zzt+2
- Şëm
- Sem
- Sěm
- Task 1
- Sém
- Encoder
- Zt
- à
- Dyn
- io
- Task 2
- u
- π
- π
- Encoder
- Task-Aware Behavior Learning
- MLLM-WM Joint Optimization
- dense reward
- action

## 箭头结构审计（arrows，advisory）

- 箭头单元 37（marker 引用 37 处，marker 定义 4 个）；头/线宽比例中位数 4.0（合理带 [1.5, 4.0]）
- F1 锚点未对齐尖端 17 处 · F2 头/线宽比例失调 6 处 · F3 端点悬空 21 处 · orient 非 auto 0 处 · 手折箭羽 0 组

### 逐条发现
- [F5] line#task-input-to-encoder start 端点 (399,56): endpoint clearance is 10.00px from declared object 'task-input-box' (expected 0.00px); nearest object is 'task-input-box'
- [F3] line#task-input-to-encoder start 端点 (399,56): endpoint boundary/gap error is 10.00px (limit 6.00px)
- [F1] line#task-input-to-encoder end 端点 (425,56) marker=arrow-gray: arrowhead tip/ref mismatch (+1.00, +0.00) px
- [F2] line#task-input-to-encoder end 端点 (425,56) marker=arrow-gray: head/stroke ratio 1.43 is outside [1.5, 4]
- [F5] line#task-input-to-encoder end 端点 (425,56) marker=arrow-gray: endpoint clearance is 10.00px from declared object 'task-encoder' (expected 0.00px); nearest object is 'task-encoder'
- [F3] line#task-input-to-encoder end 端点 (425,56) marker=arrow-gray: endpoint boundary/gap error is 10.00px (limit 6.00px)
- [F5] line#task-encoder-to-tau start 端点 (566,56): endpoint clearance is 7.00px from declared object 'task-encoder' (expected 0.00px); nearest object is 'task-encoder'
- [F3] line#task-encoder-to-tau start 端点 (566,56): endpoint boundary/gap error is 7.00px (limit 6.00px)
- [F1] line#task-encoder-to-tau end 端点 (590,56) marker=arrow-gray: arrowhead tip/ref mismatch (+1.00, +0.00) px
- [F2] line#task-encoder-to-tau end 端点 (590,56) marker=arrow-gray: head/stroke ratio 1.43 is outside [1.5, 4]
- [F5] line#task-encoder-to-tau end 端点 (590,56) marker=arrow-gray: endpoint clearance is 7.00px from declared object 'tau-box' (expected 0.00px); nearest object is 'tau-box'
- [F3] line#task-encoder-to-tau end 端点 (590,56) marker=arrow-gray: endpoint boundary/gap error is 7.00px (limit 6.00px)
- [F5] line#tau-to-mapping start 端点 (733,56): endpoint clearance is 8.00px from declared object 'tau-box' (expected 0.00px); nearest object is 'tau-box'
- [F3] line#tau-to-mapping start 端点 (733,56): endpoint boundary/gap error is 8.00px (limit 6.00px)
- [F1] line#tau-to-mapping end 端点 (758,56) marker=arrow-gray: arrowhead tip/ref mismatch (+1.00, +0.00) px
- [F2] line#tau-to-mapping end 端点 (758,56) marker=arrow-gray: head/stroke ratio 1.43 is outside [1.5, 4]
- [F5] line#tau-to-mapping end 端点 (758,56) marker=arrow-gray: endpoint clearance is 7.00px from declared object 'mapping-box' (expected 0.00px); nearest object is 'mapping-box'
- [F3] line#tau-to-mapping end 端点 (758,56) marker=arrow-gray: endpoint boundary/gap error is 7.00px (limit 6.00px)
- [F1] line#tau-to-allocator end 端点 (660,127) marker=arrow-blue: arrowhead tip/ref mismatch (+1.00, +0.00) px
- [F1] path#observation-to-mllm end 端点 (178,293) marker=arrow-orange: arrowhead tip/ref mismatch (+1.00, +0.00) px
- [F5] path#observation-to-mllm end 端点 (178,293) marker=arrow-orange: endpoint clearance is 10.00px from declared object 'mllm-encoder' (expected 0.00px); nearest object is 'mllm-encoder'
- [F3] path#observation-to-mllm end 端点 (178,293) marker=arrow-orange: endpoint boundary/gap error is 10.00px (limit 6.00px)
- [F1] path#observation-to-wm end 端点 (180,400) marker=arrow-blue: arrowhead tip/ref mismatch (+1.00, +0.00) px
- [F5] path#observation-to-wm end 端点 (180,400) marker=arrow-blue: endpoint clearance is 12.63px from declared object 'wm-encoder' (expected 0.00px); nearest object is 'wm-encoder'
- [F3] path#observation-to-wm end 端点 (180,400) marker=arrow-blue: endpoint boundary/gap error is 12.63px (limit 6.00px)
- [F1] line#mllm-to-ev end 端点 (340,274) marker=arrow-orange: arrowhead tip/ref mismatch (+1.00, +0.00) px
- [F1] line#wm-to-st end 端点 (340,435) marker=arrow-blue: arrowhead tip/ref mismatch (+1.00, +0.00) px
- [F5] line#wm-to-st end 端点 (340,435) marker=arrow-blue: endpoint clearance is 7.00px from declared object 'st-box' (expected 0.00px); nearest object is 'st-box'
- [F3] line#wm-to-st end 端点 (340,435) marker=arrow-blue: endpoint boundary/gap error is 7.00px (limit 6.00px)
- [F1] line#ev-to-joint end 端点 (421,274) marker=arrow-orange: arrowhead tip/ref mismatch (+1.00, +0.00) px
- [F5] line#ev-to-joint end 端点 (421,274) marker=arrow-orange: endpoint clearance is 7.00px from declared object 'joint-core' (expected 0.00px); nearest object is 'joint-core'
- [F3] line#ev-to-joint end 端点 (421,274) marker=arrow-orange: endpoint boundary/gap error is 7.00px (limit 6.00px)
- [F1] line#st-to-joint end 端点 (421,436) marker=arrow-blue: arrowhead tip/ref mismatch (+1.00, +0.00) px
- [F5] line#st-to-joint end 端点 (421,436) marker=arrow-blue: endpoint clearance is 7.00px from declared object 'joint-core' (expected 0.00px); nearest object is 'joint-core'
- [F3] line#st-to-joint end 端点 (421,436) marker=arrow-blue: endpoint boundary/gap error is 7.00px (limit 6.00px)
- [F1] path#allocator-task1-route-1 end 端点 (618,347) marker=arrow-blue: arrowhead tip/ref mismatch (+1.00, +0.00) px
- [F5] path#allocator-task1-route-1 end 端点 (618,347) marker=arrow-blue: endpoint clearance is 26.15px from declared object 'expert-1-dyn' (expected 0.00px); nearest object is 'expert-2-box'
- [F3] path#allocator-task1-route-1 end 端点 (618,347) marker=arrow-blue: endpoint boundary/gap error is 26.15px (limit 6.00px)
- [F1] path#allocator-task1-route-2 end 端点 (861,284) marker=arrow-blue: arrowhead tip/ref mismatch (+1.00, +0.00) px
- [F5] path#allocator-task1-route-2 end 端点 (861,284) marker=arrow-blue: endpoint clearance is 42.00px from declared object 'zt-top-box' (expected 0.00px); nearest object is 'expert-4-box'
- [F3] path#allocator-task1-route-2 end 端点 (861,284) marker=arrow-blue: endpoint boundary/gap error is 42.00px (limit 6.00px)
- [F1] path#allocator-task2-route-1 end 端点 (754,282) marker=arrow-orange: arrowhead tip/ref mismatch (+1.00, +0.00) px
- [F1] path#allocator-task2-route-2 end 端点 (862,418) marker=arrow-orange: arrowhead tip/ref mismatch (+1.00, +0.00) px
- [F5] path#allocator-task2-route-2 end 端点 (862,418) marker=arrow-orange: endpoint clearance is 41.00px from declared object 'zt-bottom-box' (expected 0.00px); nearest object is 'joint-core'
- [F3] path#allocator-task2-route-2 end 端点 (862,418) marker=arrow-orange: endpoint boundary/gap error is 41.00px (limit 6.00px)
- [F5] path#rollout-a2-to-next end 端点 (1304,333) marker=arrow-gold: endpoint clearance is 31.66px from declared object 'rollout-zh' (expected 0.00px); nearest object is 'action-a2'
- [F3] path#rollout-a2-to-next end 端点 (1304,333) marker=arrow-gold: endpoint boundary/gap error is 31.66px (limit 6.00px)
- [F5] path#mapping-to-imagination start 端点 (958,65): endpoint clearance is 7.00px from declared object 'mapping-box' (expected 0.00px); nearest object is 'mapping-box'
- [F3] path#mapping-to-imagination start 端点 (958,65): endpoint boundary/gap error is 7.00px (limit 6.00px)
- [F1] path#mapping-to-imagination end 端点 (1030,149) marker=arrow-gray: arrowhead tip/ref mismatch (+1.00, +0.00) px
- [F2] path#mapping-to-imagination end 端点 (1030,149) marker=arrow-gray: head/stroke ratio 1.43 is outside [1.5, 4]
- [F5] path#mapping-to-imagination end 端点 (1030,149) marker=arrow-gray: endpoint clearance is 28.00px from declared object 'task-conditioned-imagination' (expected 0.00px); nearest object is 'imag-z0'
- [F3] path#mapping-to-imagination end 端点 (1030,149) marker=arrow-gray: endpoint boundary/gap error is 28.00px (limit 6.00px)
- [F5] line#interaction-left start 端点 (1168,573): endpoint clearance is 20.00px from declared object 'atomic:environment-globe' (expected 0.00px); nearest object is 'atomic:environment-globe'
- [F3] line#interaction-left start 端点 (1168,573): endpoint boundary/gap error is 20.00px (limit 6.00px)
- [F1] line#interaction-left end 端点 (1015,573) marker=arrow-gray: arrowhead tip/ref mismatch (+1.00, +0.00) px
- [F2] line#interaction-left end 端点 (1015,573) marker=arrow-gray: head/stroke ratio 1.43 is outside [1.5, 4]
- [F5] line#interaction-left end 端点 (1015,573) marker=arrow-gray: endpoint clearance is 14.00px from declared object 'reward-action-box' (expected 0.00px); nearest object is 'reward-action-box'
- [F3] line#interaction-left end 端点 (1015,573) marker=arrow-gray: endpoint boundary/gap error is 14.00px (limit 6.00px)
- [F5] line#interaction-right start 端点 (1015,573): endpoint clearance is 14.00px from declared object 'reward-action-box' (expected 0.00px); nearest object is 'reward-action-box'
- [F3] line#interaction-right start 端点 (1015,573): endpoint boundary/gap error is 14.00px (limit 6.00px)
- [F1] line#interaction-right end 端点 (1177,573) marker=arrow-gray: arrowhead tip/ref mismatch (+1.00, +0.00) px
- [F2] line#interaction-right end 端点 (1177,573) marker=arrow-gray: head/stroke ratio 1.43 is outside [1.5, 4]
- [F5] line#interaction-right end 端点 (1177,573) marker=arrow-gray: endpoint clearance is 11.00px from declared object 'atomic:environment-globe' (expected 0.00px); nearest object is 'atomic:environment-globe'
- [F3] line#interaction-right end 端点 (1177,573) marker=arrow-gray: endpoint boundary/gap error is 11.00px (limit 6.00px)
- [F6] path#observation-to-wm path 端点 (159,383): arrow centerline intersects text box 'observation-label'
- [F6] path#allocator-task1-route-1 path 端点 (568,276): arrow centerline intersects text box 'expert-1-sem-label'
- [F6] path#allocator-task1-route-2 path 端点 (648,393): arrow centerline intersects text box 'expert-2-dyn-label'
- [F6] path#allocator-task2-route-2 path 端点 (734,377): arrow centerline intersects text box 'expert-3-dyn-label'
- [F6] line#imagination-z2-to-zh path 端点 (1295,182): arrow centerline intersects text box 'imag-dots'
- [F9] path#allocator-task1-route-2 path 端点 (657,406): arrow path crosses 'allocator-task2-route-1'
- [F9] path#allocator-task1-route-2 path 端点 (656,404): arrow path crosses 'allocator-task2-route-2'

> 箭头几何为定位辅助，不以本节自动放行或拦截；修复用 autofigure arrows --fix（几何归一，不改样式），头长限幅加 --clamp-ratio，按原图实测校准加 --calibrate ID=LEN，改后需重跑 convert/math/check。


## 验收状态（strict）
- blockers: 64
- region:task-guided-allocator-topology
- region:six-bicolor-state-circles
- region:rollout-arrow-topology
- region:observation-arrows
- arrow:F5:task-input-to-encoder
- arrow:F3:task-input-to-encoder
- arrow:F1:task-input-to-encoder
- arrow:F2:task-input-to-encoder
- arrow:F5:task-encoder-to-tau
- arrow:F3:task-encoder-to-tau
- arrow:F1:task-encoder-to-tau
- arrow:F2:task-encoder-to-tau
- arrow:F5:tau-to-mapping
- arrow:F3:tau-to-mapping
- arrow:F1:tau-to-mapping
- arrow:F2:tau-to-mapping
- arrow:F1:tau-to-allocator
- arrow:F1:observation-to-mllm
- arrow:F5:observation-to-mllm
- arrow:F3:observation-to-mllm
- arrow:F1:observation-to-wm
- arrow:F5:observation-to-wm
- arrow:F3:observation-to-wm
- arrow:F1:mllm-to-ev
- arrow:F1:wm-to-st
- arrow:F5:wm-to-st
- arrow:F3:wm-to-st
- arrow:F1:ev-to-joint
- arrow:F5:ev-to-joint
- arrow:F3:ev-to-joint
- arrow:F1:st-to-joint
- arrow:F5:st-to-joint
- arrow:F3:st-to-joint
- arrow:F1:allocator-task1-route-1
- arrow:F5:allocator-task1-route-1
- arrow:F3:allocator-task1-route-1
- arrow:F1:allocator-task1-route-2
- arrow:F5:allocator-task1-route-2
- arrow:F3:allocator-task1-route-2
- arrow:F1:allocator-task2-route-1
- arrow:F1:allocator-task2-route-2
- arrow:F5:allocator-task2-route-2
- arrow:F3:allocator-task2-route-2
- arrow:F5:rollout-a2-to-next
- arrow:F3:rollout-a2-to-next
- arrow:F5:mapping-to-imagination
- arrow:F3:mapping-to-imagination
- arrow:F1:mapping-to-imagination
- arrow:F2:mapping-to-imagination
- arrow:F5:interaction-left
- arrow:F3:interaction-left
- arrow:F1:interaction-left
- arrow:F2:interaction-left
- arrow:F5:interaction-right
- arrow:F3:interaction-right
- arrow:F1:interaction-right
- arrow:F2:interaction-right
- arrow:F6:observation-to-wm
- arrow:F6:allocator-task1-route-1
- arrow:F6:allocator-task1-route-2
- arrow:F6:allocator-task2-route-2
- arrow:F6:imagination-z2-to-zh
- arrow:F9:allocator-task1-route-2
- live-evidence-missing

> strict 使用关键区域、箭头结构与可选 live 回读共同门禁；全图均值不能覆盖局部失败。
