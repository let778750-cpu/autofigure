# PowerPoint 箭头创作与读回能力规格

本规格是 Autofigure 对 PowerPoint Live provider 的上游接口要求。它不修改已安装插件缓存；当前 2.1.1 仅允许 `inspect / audit / save-reopen`，不得创建或替换箭头。

## 1. 语义与枚举

- 两端独立支持 `none / open / triangle / stealth / diamond / oval`。
- COM 映射必须为 `triangle=2`、`open=3`，并执行真实创建→保存→重开→读回自检。
- 非空端点必须独立写入和读回宽度 `sm / med / lg` 与长度 `sm / med / lg`。
- 线型必须枚举并读回 12 种语义：`solid / square_dot / round_dot / dash / dash_dot / dash_dot_dot / long_dash / long_dash_dot / long_dash_dot_dot / sys_dash / sys_dot / sys_dash_dot`。
- `square_dot` 与 `round_dot` 必须结合 line cap 区分，不能只压缩成 `dashed`。

## 2. 路径与块箭头

- 原生 line、straight/elbow/curve connector 均须读回真实路径；只返回边界框不算通过。
- freeform 支持混合 line+cubic，读回必须返回保持命令顺序的规范化 `M/L/C/Z` 路径。
- AutoShape 创建须枚举实际 subtype，并读写所有有效 `Adjustments`；保存重开后再次逐值读回。
- 无法由一个原生对象表达时返回结构化错误。禁止静默生成“杆身＋独立箭头头＋group”。

## 3. capability matrix 证据

provider 只有在外部 MCP 驱动探针生成 JSON 且设置
`AUTOFIGURE_POWERPOINT_ARROW_PROBE` 后才可启用创作。证据必须绑定 server 与 bridge SHA-256，并至少包含：

- 5 个非空端点 × 3 宽 × 3 长 × start/end，共 90 个 PASS 读回；
- open/triangle 枚举自检 PASS；
- 12 种 dash PASS；
- straight/elbow/curve 与 mixed line+cubic freeform PASS；
- AutoShape subtype 和 adjustments round-trip PASS；
- PowerPoint 保存、关闭、重开后的同工件读回 PASS。

缺字段、哈希不符、只在内存中读回或版本被列为 known-bad 时一律 `arrow_authoring_allowed=false`。

## 4. Autofigure 工件身份

严格验收要求以下哈希完全相等：正式根目录 `redraw.pptx`、`bindings.artifact_sha256`、Live candidate、PowerPoint reopened artifact、`qa/powerpoint-arrow-readback.json` 的 artifact。离线 `python-pptx` reopen 只能写 `package_reopened=true`，不得写 `saved_reopened=true`。

Live 发布必须使用 `autofigure repair --evidence ... --candidate ... [--reopened ...] [--render ...]`。候选只能位于当前案例 `qa/powerpoint-live-case/`；工具先在临时影子案例重算 ArrowSpec、对象数、括号路径与 OOXML 读回，通过后才原子发布。
