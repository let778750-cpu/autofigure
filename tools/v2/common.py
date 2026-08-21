"""v2 公共约定：案例目录、哈希、路径。

每个案例一个扁平目录（参考样板项目的 per-case 约定，更精炼）：

    examples/<case>/
    ├── run.json              案例清单（case、source SHA-256、尺寸、创建时间）
    ├── reference.png         参考图（prepare 复制）
    ├── prompt.md             prepare 生成的提示词包
    ├── redraw.svg            用户从 VLM 取回的 SVG（convert 的输入）
    ├── redraw.pptx           convert 产物（原生可编辑交付物）
    ├── render.png            PowerPoint fresh render
    ├── preview.png           check 对照预览
    ├── check-report.md       check 核验报告（人审入口）
    └── qa/                   机器诊断（metrics.json / diff.png / ocr-texts.json / convert-summary.json）

案例目录即工作单元：重跑覆盖当前最佳，历史由 git 承担。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CASES_ROOT = PROJECT_ROOT / "examples"


def fail(message: str) -> SystemExit:
    return SystemExit(f"error: {message}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as image:
        return image.size


def slugify(name: str, limit: int = 32) -> str:
    """从文件名推导默认案例名：小写字母数字连字符。"""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:limit].strip("-") or "case"


@dataclass(frozen=True)
class Run:
    """一个案例目录（命名保留 Run 以避免大面积改名）。"""

    root: Path

    @property
    def meta_path(self) -> Path:
        return self.root / "run.json"

    @property
    def source_png(self) -> Path:
        return self.root / "reference.png"

    @property
    def redraw_svg(self) -> Path:
        return self.root / "redraw.svg"

    @property
    def prompt_md(self) -> Path:
        return self.root / "prompt.md"

    @property
    def build_dir(self) -> Path:
        return self.root

    @property
    def pptx_path(self) -> Path:
        return self.root / "redraw.pptx"

    @property
    def render_png(self) -> Path:
        return self.root / "render.png"

    @property
    def preview_png(self) -> Path:
        return self.root / "preview.png"

    @property
    def report_md(self) -> Path:
        return self.root / "check-report.md"

    @property
    def qa_dir(self) -> Path:
        return self.root / "qa"

    @property
    def scene_path(self) -> Path:
        return self.root / "scene.json"

    @property
    def assets_path(self) -> Path:
        return self.root / "assets.json"

    @property
    def regions_path(self) -> Path:
        return self.root / "regions.json"

    @property
    def bindings_path(self) -> Path:
        return self.root / "bindings.json"

    @property
    def region_tasks_path(self) -> Path:
        return self.qa_dir / "region-tasks.json"

    @property
    def live_request_path(self) -> Path:
        return self.qa_dir / "live-repair-request.json"

    @property
    def live_evidence_path(self) -> Path:
        return self.qa_dir / "live-evidence.json"

    @property
    def live_case_dir(self) -> Path:
        return self.qa_dir / "powerpoint-live-case"

    @property
    def live_bridge_path(self) -> Path:
        return self.qa_dir / "powerpoint-live-bridge.json"

    @property
    def layout_audit_path(self) -> Path:
        return self.qa_dir / "layout-audit.json"

    def load_meta(self) -> dict:
        if not self.meta_path.is_file():
            raise fail(f"案例清单不存在: {self.meta_path}")
        return json.loads(self.meta_path.read_text(encoding="utf-8"))


def create_run(
    reference: Path,
    case: str | None = None,
    cases_root: Path | None = None,
    *,
    source_mode: str = "svg_import",
    fidelity_profile: str = "editable_native",
) -> Run:
    reference = reference.resolve()
    if not reference.is_file():
        raise fail(f"参考图不存在: {reference}")
    case_name = case or slugify(reference.stem)
    root = (cases_root or CASES_ROOT) / case_name
    if root.exists() and any(root.iterdir()):
        raise fail(f"案例目录已存在且非空: {root}（重跑请直接覆盖文件，或先清理）")
    (root / "qa").mkdir(parents=True, exist_ok=True)
    sha = sha256_file(reference)
    width, height = image_size(reference)
    target = root / "reference.png"
    if not target.exists():
        shutil.copy2(reference, target)
    meta = {
        "case": case_name,
        "created_at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "source_abspath": str(reference),
        "source_sha256": sha,
        "width": width,
        "height": height,
    }
    (root / "run.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    run = Run(root)
    from tools.v2.contracts import initialize_contracts

    initialize_contracts(
        run,
        source_mode=source_mode,
        fidelity_profile=fidelity_profile,
    )
    return run


def open_run(run_dir: Path) -> Run:
    run = Run(run_dir.resolve())
    run.load_meta()
    from tools.v2.contracts import ContractError, initialize_contracts, validate_reference

    try:
        validate_reference(run)
        initialize_contracts(run)
    except ContractError as exc:
        raise fail(str(exc)) from exc
    return run


def main() -> int:
    sys.stdout.write("tools.v2.common 是库模块，请使用 autofigure <prepare|convert|check|math>。\n")
    return 0
