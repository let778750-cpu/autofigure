# 09 · Scientific Illustrator 后端复用契约

> 来源：上游 `scientific-illustrator` v1.5.4（commit `3a44435`）+ 副本 `scientific-illustrator-backend.md`。
> 本 skill 复用官方插件的 MCP，不在本目录内置另一套 MCP 实现或角色正文。

## 复用边界

| 能力 | 复用 |
|---|---|
| PowerPoint/WPS 绘图 | `powerpoint-live` MCP（`powerpoint_*` 工具：launch/status/get_capabilities/new_presentation/add_textbox/add_shape/add_line/add_connector/add_table/add_chart/add_image/update_shape/align/distribute/set_z_order/group/ungroup/audit_figure/export_slide_image/save/...） |
| draw.io 绘图（可选） | `drawio-live` MCP（`drawio_live_*`） |
| 文件校验/导出 | `drawio-file-utils` MCP（`drawio_validate`/`drawio_export`） |

AI autofigure 只增加：已确认 PNG 入口门、原生视觉分解约束、人工素材槽、双轴 QA、来源冲突裁决。不复制上游的 MCP 代码 / 工具名清单 / 四角色正文。

## 画布尺寸前置条件

上游 v1.5.4 协议要求先确定页面尺寸，但公开 PowerPoint 工具只读 `PageSetup`，未提供可靠的 slide-size setter。AutoFigure 因此必须在绘制前：

1. 从参考图实测宽高比；
2. 用 `tools/create_canvas_pptx.py` 创建该比例的空白 deck；
3. 通过 `powerpoint_launch(file_path=...)` 打开它；
4. 读回 PageSetup，确认宽高比与 frozen spec 一致后才构建对象。

禁止用 `powerpoint_new_presentation` 的默认 16:9/4:3 页面承担任意参考图复刻，也禁止绘制后缩放整页补救。

## 会话规则

1. 先读状态和能力，再选对象类型。
2. 默认打开当前 run 内、按参考比例预先创建的隔离文稿；编辑现有文稿前先检查，默认另存副本，不修改无关已打开文件。
3. 默认后台安全更新，不抢占桌面焦点；只有用户要求观看过程时才切换前台策略。
4. 每区域同时取结构读回 + 当前渲染；必须实际查看最新渲染，不得仅凭工具返回成功继续。
5. 修正只操作 finding 指向的最小对象；禁止截图替换区域。
6. 保存后重新检查：文件可打开、对象仍可编辑、预览与当前源一致。

## 后端优先级

- 默认 PowerPoint；Windows 优先实时 COM；其他平台按官方插件报告的能力用 Office.js 或诚实标记的 OOXML fallback。
- 不能把「生成了 PPTX 文件」等同于「连接到了用户正在看的实时 PowerPoint」。
- 后端不可用时停止绘制并告知用户安装/启用本地 `scientific-illustrator`，不临时手写另一套自动化绕过依赖。

## 原生公式 capability gap 与事务桥接

当前 scientific-illustrator PowerPoint 协议把 formula label 映射到 `powerpoint_add_textbox`，公开能力中没有一等 Office Math/OMML 插入与 readback 接口。这是明确的 capability gap：textbox 即使内容长得像 LaTeX，也不是可编辑的 PowerPoint 公式，不能用于本项目的公式对象。

公式必须走项目内 `tools/powerpoint_native_math.py`，其职责限于 native PPTX 语义，不复制 scene/spec 真值：

1. Designer 先对每条 canonical LaTeX 执行 compile-only 转换，冻结 `NATIVE_OFFICE_MATH_CONVERTER_RECEIPT`；receipt 包含 canonical LaTeX/hash、精确编译 OMML hash、`office-math-semantic-v2` hash、转换器版本和 Office XSL hash。preflight 只在全部 receipt PASS 且哈希闭合时授权 Drawer。
2. Drawer 用 powerpoint-live 构建普通原生对象，并为每条 display/inline 公式保留稳定、唯一、formula-ID 绑定的 placeholder；不得把 placeholder 文本计为公式完成。
3. 保存后关闭 deck，确认 PowerPoint/COM 已释放该文件。随后在 run 内候选副本上事务式注入 `a14:m` + OMML；每个 math run 必须由 injection plan 绑定 `formula_id + receipt_path + receipt_sha256`。注入函数只返回 `INJECTED_REQUIRES_POWERPOINT_ROUNDTRIP`，验证 ZIP/关系/shape 计数后也不能自授最终 PASS。禁止对 PowerPoint 正打开的包做 OOXML 修改。
4. 脱离运行事务的 JSON receipt 只可作为日志，不能授权通过。唯一机械门禁是 `tools/powerpoint_native_math.py finalize`：父进程生成随机 challenge，直接启动固定的 `tools/powerpoint_native_math_roundtrip.ps1` 子进程，并在子进程退出后立即同时核对 challenge、父/子 PID、input/output/plan/injection report/PowerShell/Python 哈希和 stdout/临时 receipt 完全一致。PowerPoint 在 staging 文件上另存、关闭、只读重开；枚举并绑定每个公式 shape 内 MathZone 的顺序、字符范围和文本哈希，检查可见性、边界、透明度、z-order、图片/OLE/普通文本公式冒充，丢弃首次 warm-up 后连续导出两张 PNG；随后针对每个 MathZone 分别重新只读打开 staging 文件、仅把该 zone 设为透明并导出控制图。父进程要求基线渲染尺寸与 RGBA 像素哈希一致，并要求每条公式的控制图在所属 shape 内有足量变化、shape 外无变化。静态结构审计只能为 `STRUCTURE_PASS_REQUIRES_POWERPOINT_FINALIZE`。
5. 每条公式 readback 必须同时满足：唯一 shape/formula ID；`a14:m` 是 `p:txBody/a:p` 的直接子；根为 mode 对应的 `m:oMath` 或 `m:oMathPara`；canonical LaTeX/hash、formula ID 与外部 plan/receipt 一致；`office-math-semantic-v2` hash 一致；混合 text/math run 的文字、边界与顺序一致；MathZone 数量、顺序、字符范围和文本哈希一致；shape 可见、不透明、在画布内且无高层遮挡；无 textbox/SVG/PNG/JPEG/EMF/OLE masquerade；fresh render、逐公式反事实控制图与最终 PPTX 哈希闭合。PowerPoint 会合法补样式、拆分 run 和使用数学字母 Unicode，因此编译期精确 OMML hash 保留作来源证据，但保存后的字节哈希预期可变。任一语义或结构缺失是 hard FAIL；成功也只能返回 `MECHANICAL_GATE_PASS_REQUIRES_INDEPENDENT_REVIEW`，不能自授发布级 PASS/APPROVED。

严格模式没有公式降级路径。原生转换、事务注入、PowerPoint 重开或 readback 任一步失败时，候选保持 `INCONCLUSIVE/FAIL` 并回退到最近的完整 PPTX；finalize 必须写入新的 run 路径，拒绝覆盖既有输出。不得静默改用 PNG、SVG、EMF、普通文本或“可恢复 LaTeX 备注”。公式编号和 Office 不支持的 LaTeX 布局应拆成明确可编辑对象或回到 Designer 决策。同一权限域中的持久 JSON 不是不可伪造的密码学证明；若交付链要求不可抵赖，必须增加受保护的外部签名/可信独立执行服务。

公式 finalizer 只证明所提交 plan/receipt 中的公式对象是原生、可编辑、可见并与该 authority set 一致，不取代整图门禁。当前 injection plan 不重复承载 frozen figure spec 的完整 bbox/字号合同，启发式 masquerade 扫描也不保证识别所有非重叠中性命名图片、Unicode 数学普通文本或多片微小遮挡。编排器必须把公式报告、G1/G2 preflight、整图 native readback 和 fresh render 一并交给 hash-bound 独立 Reviewer；缺少外层证据时，即使公式机械子门通过也不得晋级。

## MCP 注册

`mcp.json` 已给出 3 个 server 的注册定义（`powerpoint-live` / `drawio-live` / `drawio-file-utils`），命令指向本机 `scientific-illustrator` 插件的 `scripts/*.mjs`。Claude Code 中按 `mcp.json` 注册即可。

这些 server 的 `cwd` 是项目外的插件目录。所有 save/export 调用必须传入由当前 `examples/generated/runs/<run_id>/` 解析得到的绝对路径；禁止把相对路径交给 MCP，否则 Draw.io 工具可能相对插件目录落盘。PowerPoint 工具即使自行要求 absolute，也必须使用同一条 run-bound 路径合同。
