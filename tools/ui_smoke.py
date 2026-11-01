"""UI smoke test on Windows (CI): starts the overlay from the sources, refreshes the
display and checks that the per-monitor DPI scale is detected and applied.

Usage: python tools/ui_smoke.py OUT.txt"""
import importlib.util
import os
import sys

out = sys.argv[1]
spec = importlib.util.spec_from_file_location("ov", os.path.join(os.path.dirname(__file__), "..", "overlay.py"))
ov = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ov)
ov.get_json = lambda url: {"current": {"temperature_2m": 14.3, "weather_code": 0}}
lines = []
o = ov.Overlay()


def check():
    try:
        o.refresh_display()
        o.root.update()
        s = ov.window_scale(o.root)
        lines.append(f"window_scale={s} SCALE={ov.SCALE} W={ov.W} H={ov.H} "
                     f"win={o.root.winfo_width()}x{o.root.winfo_height()} font={ov.FONT}")
        ok = s is not None and abs(s - ov.SCALE) < 0.01 and o.root.winfo_width() == ov.W
        lines.append("UI OK" if ok else "UI FAIL")
    except Exception as ex:
        lines.append(f"UI FAIL {type(ex).__name__}: {ex}")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    o.root.destroy()


o.root.after(1500, check)
o.run()
