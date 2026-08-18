"""PowerPoint COM fresh render：PPTX → 指定尺寸 PNG（pywin32 直驱，无 PowerShell）。"""

from __future__ import annotations

from pathlib import Path


def render(pptx_path: Path, out_png: Path, width: int, height: int) -> None:
    import pythoncom
    import win32com.client

    pptx_abs = str(pptx_path.resolve())
    out_abs = str(out_png.resolve())
    out_png.parent.mkdir(parents=True, exist_ok=True)

    pythoncom.CoInitialize()
    app = None
    pres = None
    try:
        app = win32com.client.Dispatch("PowerPoint.Application")
        # ReadOnly=True, Untitled=False, WithWindow=False
        pres = app.Presentations.Open(pptx_abs, True, False, False)
        pres.Slides.Item(1).Export(out_abs, "PNG", width, height)
    finally:
        if pres is not None:
            pres.Close()
        if app is not None:
            app.Quit()
        pythoncom.CoUninitialize()

    if not out_png.is_file():
        raise RuntimeError(f"PowerPoint render 未产出: {out_png}")
