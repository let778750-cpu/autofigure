"""autofigure prepare — 显式选择输入路线并建立哈希绑定案例。"""

from __future__ import annotations

import argparse
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from tools import common
from tools.contracts import FIDELITY_PROFILES, INPUT_ROUTES, PROCESSING_MODES

SVG_AUTHORING_CONTRACT = """【硬性要求】
1. `<svg>` 根元素必须带 `width="{width}" height="{height}" viewBox="0 0 {width} {height}"`，所有坐标以原图像素为基准，不得缩放；还必须从本案例合同逐字填写
   `data-source-schema-version="4.0.0" data-case="..." data-reference-sha256="..." data-object-inventory-sha256="..." data-stable-element-ids="true" data-relations-exhaustive="true"`。
2. 所有文字必须逐字照抄原图（含大小写、上下标、希腊字母、标点），用 `<text>`/`<tspan>` 表达；禁止把文字画成路径。
3. 公式用文本表达：变量斜体 `font-style="italic"`，上下标用 `<tspan baseline-shift="sub|super">`。
4. 照片、真实场景截图、复杂写实图标，以及不含文字且无法高质量矢量还原的写实装饰元素：不要重绘，放占位矩形
   `<rect id="atomic:<语义名>" x=".." y=".." width=".." height=".." fill="#EEEEEE" stroke="#999999" stroke-dasharray="4 3"/>`，
   位置尺寸与原图该区域一致。含文字/公式的内容、几何图形、线条、箭头禁止占位。
5. 箭头的粗细、头部样式（实心/开放/块状）、尺寸、弯折位置与连接关系一律以原图为准，不得套用固定风格：
   实心头用填充 marker 或整体轮廓 path，开放折线头用描边 marker，块状/楔形/弯折箭头画单一闭合整体轮廓。必须先逐条清点
   `source → target + direction`，同时冻结参考图的可见图元数量：参考若是一根连续双端杆身或一个双端块箭头，必须只生成一个 SVG 元素、
   一个双端 ArrowSpec 和一个 PowerPoint 可见对象，禁止拆成两条共线反向单头箭头；参考若明确画出两条独立反向关系，则必须走可分辨的
   不同车道，禁止重叠中心线。共享目标的独立关系不得省略，但也不得把一个双向图元错误复制成两个对象。
6. 版面纪律：文字与文字、文字与图形不得重叠；箭头与连接线端点落在形状边缘或间隙，不得压盖文字；
   连接线不与沿途文字相交；元素间距与留白以原图为准。
7. 布局必须可审计：容器内的文字/公式须有稳定 `id`，并标注
   `data-layout-container="<容器id>" data-layout-padding="<像素>"`；重复圆/节点等同级图元须逐个有稳定 `id`，并标注相同
   `data-repeat-group`、`data-repeat-axis="vertical|horizontal"` 和唯一 `data-repeat-order`。同组图元须等尺寸、同轴，中心距差最多 1 px。
8. 细节关系必须显式声明：等尺寸但非等距的同类框使用 `data-peer-size-group`，组内每个成员必须声明相同且不超过 1 px 的
   `data-peer-size-tolerance`；位于相邻对象间隙的箭头使用
   `data-gap-source-id`、`data-gap-target-id`、`data-gap-axis` 和两端 inset，使长度随两侧边界自适应；必须可见在某对象上/下层时使用
   `data-z-above`/`data-z-below`。纵向单词须区分整词旋转 `data-text-flow="rotated-word"` 与逐字直立堆叠
   `data-text-flow="stacked-characters"`，并用 `data-text-container` 声明所属框；逐字堆叠还必须显式声明
   `data-text-stack-step`。若仅为容纳 PowerPoint 的透明文本选择框而需越出所属框，可显式声明不超过 3 px 的
   `data-text-frame-overflow-tolerance`；该容差不得用于放行可见字形溢出。禁止仅凭外观混用两种实现。
9. 渐变用 `<linearGradient>`；虚线用 `stroke-dasharray`；成组元素用 `<g>`。
10. 只输出 SVG 代码本身，不要任何解释文字。
"""

PROMPT_TEMPLATE = (
    "你是一名科研图表重绘专家。附件是一篇论文的模型架构图（{width}×{height} 像素）。"
    "请将它重绘为一个 SVG 文件，严格遵守以下输出合同。\n\n"
    + SVG_AUTHORING_CONTRACT
    + "\n【风格要求】\n"
    "- 颜色、字号、粗细、对齐、间距尽量贴近原图。\n"
    "- 先整体布局后局部细节，确保每个模块位置与原图一一对应。\n"
)

FOLLOW_UP = """
下一步：
1. 仅依据本案例 reference.png 完成 regions.json 的闭世界对象清单、关键区、关系与视觉合同；
2. 运行：autofigure freeze "{run_root}"；未冻结或 receipt 漂移时禁止接收候选；
3. 外部 SVG 已作为 external-seed.svg 哈希冻结；freeze 后 source gate 会决定
   svg_import、svg_repair 或 png_reconstruct，禁止把种子直接改名为 redraw.svg；
4. 若 gate=repair，按 qa/source-gate-report.json 生成受限修复候选，再运行 ingest；
5. 然后运行：autofigure convert "{run_root}"
"""

PNG_RECONSTRUCT_PROMPT = (
    """# PNG-only reconstruction contract

本案例从冻结的 `reference.png` 直接开始，不要求、也不依赖 GPT Web 或其他网页端预先生成 SVG。

- 画布：{width}×{height} px
- 参考图 SHA-256：`{reference_sha256}`
- 区域任务：`{region_tasks_path}`
- 默认保真策略：`hybrid_fidelity`

执行者可以是 Codex、其他 VLM 或人工操作员。必须逐区读取参考图并保持稳定对象 ID；文字、公式、规则形状和箭头保持原生可编辑。只有在 `assets.json` 明确授权时，复杂且不可忠实矢量化的微资产才允许使用紧边界参考图裁剪。

开始绘制前，必须仅依据本案例 `reference.png` 冻结每个关键区的对象清单和逐条有向关系；箭头关系写入 `regions.json.required_relations`，每条显式包含 `id/source_id/target_id/direction/start_head_type/end_head_type/representation/visible_object_count`，再刷新区域任务。该清单是后续 scene、bindings 与 PowerPoint 保存重开门禁的闭世界输入；未先声明的细小箭头无法靠大区域 SSIM 自动发现。

## SVG 作者硬性合同（与 svg-seeded 路线共用）

工具会同时审计 SVG 源坐标和保存重开的 PowerPoint shape，借此区分视觉测量错误与转换漂移。
无论执行者是谁，返回的 SVG 载体都必须满足与 svg-seeded 路线完全相同的输出合同：

"""
    + SVG_AUTHORING_CONTRACT
    + """
候选通过 `autofigure ingest` 返回。离线初版当前以 SVG 作为可渲染载体；完整 scene/region patch 可以用于已有载体的修复，或交给 PowerPoint Live provider。任务协议与模型品牌无关，但视觉推理仍需要模型或人工执行，不能把“入口已连通”误写成“PNG 已自动一比一重建”。
"""
)

PNG_RECONSTRUCT_FOLLOW_UP = """
下一步：
1. 仅依据本案例 reference.png 完成 regions.json 的闭世界对象清单、关键区、关系与视觉合同；
2. 运行 `autofigure freeze "{run_root}"`，生成哈希绑定 receipt 并刷新区域任务；
3. 读取冻结参考图和区域任务：
   {reference_path}
   {region_tasks_path}
4. 由任意具备视觉能力的执行者生成候选；不要求使用 GPT Web。
5. 离线 SVG 候选执行：
   autofigure ingest "{run_root}" "<candidate.svg>" --kind svg
   autofigure convert "{run_root}"
6. scene/region patch 仅在已有可渲染载体或 PowerPoint Live 修复会话中使用。
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure prepare", description=__doc__)
    parser.add_argument("reference", type=Path, help="参考图 PNG 路径")
    parser.add_argument("--case", default=None, help="案例名（默认从文件名推导）")
    parser.add_argument("--cases-root", type=Path, default=None, help="案例根目录（默认项目 examples/）")
    parser.add_argument(
        "--input-route",
        choices=INPUT_ROUTES,
        required=True,
        help="不可变输入路线：reference-only 或 svg-seeded",
    )
    parser.add_argument(
        "--seed",
        type=Path,
        default=None,
        help="svg-seeded 必填的唯一外部 SVG；reference-only 禁止提供",
    )
    parser.add_argument(
        "--source-mode",
        choices=PROCESSING_MODES,
        default=None,
        help="已弃用；仅校验与 --input-route 推导的初始处理模式一致",
    )
    parser.add_argument(
        "--fidelity-profile",
        choices=FIDELITY_PROFILES,
        default=None,
        help="交付策略；省略时 png_reconstruct 默认 hybrid_fidelity，其余默认 editable_native",
    )
    args = parser.parse_args(argv)

    if args.input_route == "svg-seeded" and args.seed is None:
        raise common.fail("svg-seeded 新案例必须通过 --seed 同时提供唯一外部 SVG")
    if args.input_route == "reference-only" and args.seed is not None:
        raise common.fail("reference-only 案例禁止提供 --seed")
    seed_path: Path | None = None
    if args.seed is not None:
        seed_path = args.seed.resolve()
        if not seed_path.is_file():
            raise common.fail(f"外部 SVG 种子不存在: {seed_path}")
        try:
            root = ET.parse(seed_path).getroot()
        except ET.ParseError as exc:
            raise common.fail(f"外部 SVG 种子无效: {exc}") from exc
        if not root.tag.endswith("svg"):
            raise common.fail("--seed 必须是 SVG 根文档")

    processing_mode = (
        "png_reconstruct" if args.input_route == "reference-only" else "svg_import"
    )
    if args.source_mode is not None:
        sys.stderr.write(
            "warning: --source-mode 已弃用；输入来源只由必填 --input-route 记录。\n"
        )
        if args.source_mode != processing_mode:
            raise common.fail(
                f"--source-mode {args.source_mode} 与输入路线 {args.input_route} 的"
                f"初始模式 {processing_mode} 不一致"
            )
    fidelity_profile = args.fidelity_profile or (
        "hybrid_fidelity" if args.input_route == "reference-only" else "editable_native"
    )
    run = common.create_run(
        args.reference,
        case=args.case,
        cases_root=args.cases_root,
        input_route=args.input_route,
        processing_mode=processing_mode,
        fidelity_profile=fidelity_profile,
    )
    meta = run.load_meta()
    if seed_path is not None:
        shutil.copy2(seed_path, run.external_seed_svg)
        from tools.contracts import record_candidate_provenance, write_json

        record_candidate_provenance(
            run,
            run.external_seed_svg,
            kind="svg",
            origin="unknown",
            role="external-seed",
            canonical_path="external-seed.svg",
        )
        write_json(
            run.source_gate_report_path,
            {
                "schema_version": "4.0.0",
                "kind": "source_gate_report",
                "case": meta["case"],
                "input_route": "svg-seeded",
                "reference_sha256": meta["source_sha256"],
                "seed_sha256": common.sha256_file(run.external_seed_svg),
                "status": "pending",
                "decision": None,
                "reason_codes": ["reference-inventory-not-frozen"],
                "protected_element_ids": [],
                "repair_region_ids": [],
            },
        )
    try:
        portable_run_root = run.root.relative_to(common.PROJECT_ROOT)
    except ValueError:
        portable_run_root = run.root
    if args.input_route == "reference-only":
        from tools.ingest import build_region_tasks

        build_region_tasks(run)
        prompt = PNG_RECONSTRUCT_PROMPT.format(
            width=meta["width"],
            height=meta["height"],
            reference_sha256=meta["source_sha256"],
            region_tasks_path="qa/region-tasks.json",
        )
        follow_up = PNG_RECONSTRUCT_FOLLOW_UP.format(
            reference_path="reference.png",
            region_tasks_path="qa/region-tasks.json",
            run_root=portable_run_root,
        )
    else:
        prompt = PROMPT_TEMPLATE.format(width=meta["width"], height=meta["height"])
        follow_up = FOLLOW_UP.format(
            run_root=portable_run_root,
        )
    run.prompt_md.write_text(prompt, encoding="utf-8")

    sys.stdout.write(f"案例已创建: {run.root}\n")
    sys.stdout.write(f"提示词包: {run.prompt_md}\n")
    sys.stdout.write(follow_up + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
