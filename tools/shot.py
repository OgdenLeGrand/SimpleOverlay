"""Takes a screenshot of the overlay (from the sources) to check the rendering on Windows
and to produce the README screenshot.

Usage: python tools/shot.py OUT.png SCALE CITY
"""
import importlib.util
import os
import sys

out, scale, city = sys.argv[1], sys.argv[2], sys.argv[3]
os.environ["SIMPLEOVERLAY_SCALE"] = scale
os.environ.setdefault("SIMPLEOVERLAY_LANG", "en")
spec = importlib.util.spec_from_file_location("ov", os.path.join(os.path.dirname(__file__), "..", "overlay.py"))
ov = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ov)

# Realistic sample values
ov.get_json = lambda url: {"current": {"temperature_2m": 14.3, "weather_code": 1}}
ov.gpu_stats = lambda: (64, 37, 52.0)
ov.psutil.cpu_percent = lambda *a, **k: 18.0


class _Mem:
    percent = 46.0


ov.psutil.virtual_memory = lambda: _Mem()


class HW:
    cpu, gpu, gpu_load, gpu_vram, state = 58.0, None, None, None, "ok"

    def __init__(self, **kw):
        pass


ov.HWSensors = HW
margin = int(28 * float(scale))
ov.load_cfg = lambda: {**ov.DEFAULT_CFG, "city": city, "x": 200 + margin, "y": 200 + margin, "alpha": 1.0}
o = ov.Overlay()

# Neutral backdrop behind the overlay, so the rounded corners look right on the screenshot
bg = ov.tk.Toplevel(o.root)
bg.overrideredirect(True)
bg.configure(bg="#2a2c31")
bg.geometry(f"{ov.W + 2 * margin}x{ov.H + 2 * margin}+200+200")
bg.lower(o.root)


def shot():
    from PIL import ImageGrab
    o.root.lift()
    o.root.update()
    ImageGrab.grab(bbox=(200, 200, 200 + ov.W + 2 * margin, 200 + ov.H + 2 * margin)).save(out)
    print("font:", ov.FONT)
    o.root.destroy()


o.root.after(2500, shot)
o.run()
