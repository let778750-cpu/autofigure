"""v2 公共约定：run 目录、哈希、路径。

每个 run 的目录布局：

    examples/generated/runs/v2-<UTC>-<sha8>/
    ├── run.json              元数据（run_id、source SHA-256、尺寸、创建时间）
    ├── input/source.png      参考图拷贝
    ├── input/redraw.svg      用户从 GPT 取回的 SVG（convert 的输入）
    ├── prompt/prompt.md      prepare 生成的提示词包
    ├── build/redraw.pptx     convert 产物（原生可编辑）
    ├── build/render.png      PowerPoint fresh render
    └── qa/                   check 产物（metrics.json / diff.png / preview.png / text-diff.md）
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = PROJECT_ROOT / "examples" / "generated" / "runs"


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


@dataclass(frozen=True)
class Run:
    root: Path

    @property
    def meta_path(self) -> Path:
        return self.root / "run.json"

    @property
    def source_png(self) -> Path:
        return self.root / "input" / "source.png"

    @property
    def redraw_svg(self) -> Path:
        return self.root / "input" / "redraw.svg"

    @property
    def prompt_md(self) -> Path:
        return self.root / "prompt" / "prompt.md"

    @property
    def build_dir(self) -> Path:
        return self.root / "build"

    @property
    def pptx_path(self) -> Path:
        return self.build_dir / "redraw.pptx"

    @property
    def render_png(self) -> Path:
        return self.build_dir / "render.png"

    @property
    def qa_dir(self) -> Path:
        return self.root / "qa"

    def load_meta(self) -> dict:
        if not self.meta_path.is_file():
            raise fail(f"run 元数据不存在: {self.meta_path}")
        return json.loads(self.meta_path.read_text(encoding="utf-8"))


def create_run(reference: Path, runs_root: Path | None = None) -> Run:
    reference = reference.resolve()
    if not reference.is_file():
        raise fail(f"参考图不存在: {reference}")
    sha = sha256_file(reference)
    width, height = image_size(reference)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"v2-{stamp}-{sha[:8]}"
    root = (runs_root or RUNS_ROOT) / run_id
    if root.exists():
        raise fail(f"run 目录已存在（同一分钟内同图）: {root}")
    (root / "input").mkdir(parents=True)
    (root / "prompt").mkdir()
    shutil.copy2(reference, root / "input" / "source.png")
    meta = {
        "run_id": run_id,
        "created_at": stamp,
        "source_abspath": str(reference),
        "source_sha256": sha,
        "width": width,
        "height": height,
    }
    (root / "run.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return Run(root)


def open_run(run_dir: Path) -> Run:
    run = Run(run_dir.resolve())
    run.load_meta()
    return run


def main() -> int:
    sys.stdout.write("tools.v2.common 是库模块，请使用 autofigure <prepare|convert|check|math>。\n")
    return 0
