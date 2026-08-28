"""由 Paddle 解释器执行的极简 OCR：对图片跑一次识别，文本列表写入 JSON。

用法（仅供 tools.check 内部调用）：
    D:\\paddle ocr\\env\\python.exe ocr_texts.py <ocr-config.json> <image> <out.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    config_path, image_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    models_root = Path(cfg["paddle_root"]) / cfg["models_root_relative_path"]
    models = cfg["models"]

    from paddleocr import PaddleOCR
    import paddle

    device = "gpu:0" if paddle.device.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0 else "cpu"
    ocr = PaddleOCR(
        text_detection_model_name=models["text_detection"]["name"],
        text_detection_model_dir=str(models_root / models["text_detection"]["relative_path"]),
        text_recognition_model_name=models["text_recognition"]["name"],
        text_recognition_model_dir=str(models_root / models["text_recognition"]["relative_path"]),
        textline_orientation_model_name=models["textline_orientation"]["name"],
        textline_orientation_model_dir=str(models_root / models["textline_orientation"]["relative_path"]),
        **cfg["inference"],
        device=device,
        enable_hpi=False,
        use_tensorrt=False,
    )
    texts: list[str] = []
    for result in ocr.predict(image_path):
        payload = result if isinstance(result, dict) else result.json
        res = payload.get("res", payload)
        texts.extend(str(t) for t in res.get("rec_texts", []))

    Path(out_path).write_text(
        json.dumps(texts, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
