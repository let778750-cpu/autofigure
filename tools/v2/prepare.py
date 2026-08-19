"""autofigure prepare — 建案例目录并生成多模态大模型网页端提示词包。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tools.v2 import common

PROMPT_TEMPLATE = """你是一名科研图表重绘专家。附件是一篇论文的模型架构图（{width}×{height} 像素）。请将它重绘为一个 SVG 文件，严格遵守以下输出合同。

【硬性要求】
1. `<svg>` 根元素必须带 `width="{width}" height="{height}" viewBox="0 0 {width} {height}"`，所有坐标以原图像素为基准，不得缩放。
2. 所有文字必须逐字照抄原图（含大小写、上下标、希腊字母、标点），用 `<text>`/`<tspan>` 表达；禁止把文字画成路径。
3. 公式用文本表达：变量斜体 `font-style="italic"`，上下标用 `<tspan baseline-shift="sub|super">`。
4. 照片、真实场景截图、复杂写实图标，以及不含文字且无法高质量矢量还原的写实装饰元素：不要重绘，放占位矩形
   `<rect id="atomic:<语义名>" x=".." y=".." width=".." height=".." fill="#EEEEEE" stroke="#999999" stroke-dasharray="4 3"/>`，
   位置尺寸与原图该区域一致。含文字/公式的内容、几何图形、线条、箭头禁止占位。
5. 箭头的粗细、头部样式（实心/开放/块状）、尺寸、弯折位置与连接关系一律以原图为准，不得套用固定风格：
   实心头用填充 marker 或整体轮廓 path，开放折线头用描边 marker，块状/楔形/弯折箭头画整体轮廓 path。
6. 版面纪律：文字与文字、文字与图形不得重叠；箭头与连接线端点落在形状边缘或间隙，不得压盖文字；
   连接线不与沿途文字相交；元素间距与留白以原图为准。
7. 渐变用 `<linearGradient>`；虚线用 `stroke-dasharray`；成组元素用 `<g>`。
8. 只输出 SVG 代码本身，不要任何解释文字。

【风格要求】
- 颜色、字号、粗细、对齐、间距尽量贴近原图。
- 先整体布局后局部细节，确保每个模块位置与原图一一对应。
"""

FOLLOW_UP = """
下一步：
1. 打开多模态大模型网页端（GPT / Kimi / Claude 等），上传参考图，并把上面 prompt.md 的全文作为指令发送；
2. 取回 SVG 代码，保存为：
   {svg_path}
3. 然后运行：autofigure convert "{run_root}"
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure prepare", description=__doc__)
    parser.add_argument("reference", type=Path, help="参考图 PNG 路径")
    parser.add_argument("--case", default=None, help="案例名（默认从文件名推导）")
    parser.add_argument("--cases-root", type=Path, default=None, help="案例根目录（默认项目 examples/）")
    args = parser.parse_args(argv)

    run = common.create_run(args.reference, case=args.case, cases_root=args.cases_root)
    meta = run.load_meta()
    prompt = PROMPT_TEMPLATE.format(width=meta["width"], height=meta["height"])
    run.prompt_md.write_text(prompt, encoding="utf-8")

    sys.stdout.write(f"案例已创建: {run.root}\n")
    sys.stdout.write(f"提示词包: {run.prompt_md}\n")
    sys.stdout.write(
        FOLLOW_UP.format(svg_path=run.redraw_svg, run_root=run.root) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
