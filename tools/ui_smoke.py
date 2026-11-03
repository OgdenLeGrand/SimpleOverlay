"""UI smoke test on Windows (CI): starts the overlay from the sources, refreshes the
display and checks that the per-monitor DPI scale is detected and applied, and that
the window is put back on top when it loses its "always on top" state.

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
        # always on top: must be OK now; then drop the window out of the topmost band,
        # the check must detect it and put it back
        import ctypes
        u = ov._user32()
        hwnd = ov._root_hwnd(o.root)
        before = ov.topmost_problem(o.root)
        u.SetWindowPos(hwnd, ctypes.c_void_p(-2), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # HWND_NOTOPMOST
        problem = ov.ensure_topmost(o.root)
        after = ov.topmost_problem(o.root)
        o._reassert_topmost()
        forced = ov.topmost_problem(o.root)
        lines.append(f"topmost: before={before} problem={problem} after={after} forced={forced}")
        ok = ok and before is None and bool(problem) and after is None and forced is None
        lines.append("UI OK" if ok else "UI FAIL")
    except Exception as ex:
        lines.append(f"UI FAIL {type(ex).__name__}: {ex}")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    o.root.destroy()


o.root.after(1500, check)
o.run()
