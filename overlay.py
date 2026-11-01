#!/usr/bin/env python3
"""
SimpleOverlay - a tiny, discreet system overlay for Windows:
GPU / CPU temperature, GPU / VRAM / CPU / RAM usage and the current weather of a city.
https://github.com/OgdenLeGrand/SimpleOverlay

Requirements:   pip install psutil nvidia-ml-py certifi pythonnet
Run:            pythonw overlay.py      (Windows, no console)
                python3 overlay.py      (Linux / macOS, without the Windows-only features)

Usage:
  - Left-click and drag to move the overlay (the position is remembered)
  - Double-click the weather to pick another city
  - Right-click for the menu (city, opacity, start with Windows, CPU temperature, updates, quit)

Weather comes from Open-Meteo (free, no API key) and refreshes every 10 minutes.
Settings are stored in ~/.mini_overlay.json, errors are logged to ~/.simpleoverlay.log
"""
import json
import os
import queue
import ssl
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.parse
import urllib.request

import psutil

# Current version: bumping it makes the CI build and publish a new GitHub release
VERSION = "1.8.0"
REPO = "OgdenLeGrand/SimpleOverlay"
EXE_NAME = "SimpleOverlay.exe"
UPDATE_EVERY_MS = 6 * 60 * 60 * 1000

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
FROZEN = getattr(sys, "frozen", False)


# ---------- user interface language (follows Windows: French or English) ----------
def _detect_lang():
    forced = os.environ.get("SIMPLEOVERLAY_LANG", "")
    if forced:
        return forced[:2].lower()
    if IS_WIN:
        try:
            import ctypes
            primary = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
            return "fr" if primary == 0x0C else "en"  # 0x0C = LANG_FRENCH
        except Exception:
            pass
    import locale
    loc = (locale.getlocale()[0] or os.environ.get("LANG", "")).lower()
    return "fr" if loc.startswith("fr") else "en"


LANG = _detect_lang()

STRINGS = {
    "en": {
        "change_city": "Change city…",
        "refresh_weather": "Refresh weather",
        "refresh_display": "Refresh display",
        "opacity": "Opacity",
        "autostart": "Start with Windows",
        "cpu_running": "Setting up…",
        "cpu_failed": "● CPU temperature: failed, retry",
        "cpu_enable": "Enable CPU temperature (admin)…",
        "cpu_loading": "CPU temperature: loading…",
        "cpu_ok": "CPU temperature ✓",
        "cpu_repair": "● Repair CPU temperature…",
        "check_update": "v{v} · Check for updates",
        "script": "v{v} (script)",
        "quit": "Quit",
        "searching": "Searching…",
        "cant_check": "v{v} · Couldn't check",
        "update_to": "● Update to {tag}",
        "up_to_date": "v{v} · Up to date ✓",
        "downloading": "Downloading…",
        "updating": "updating…",
        "update_failed": "● Update failed, retry",
        "city_title": "City",
        "city_hint": "Type a city, then press Enter",
        "net_error": "Network error",
        "no_result": "No results",
    },
    "fr": {
        "change_city": "Changer de ville…",
        "refresh_weather": "Actualiser la météo",
        "refresh_display": "Rafraîchir l'affichage",
        "opacity": "Opacité",
        "autostart": "Lancer au démarrage",
        "cpu_running": "Configuration en cours…",
        "cpu_failed": "● Température CPU : échec, réessayer",
        "cpu_enable": "Activer la température CPU (admin)…",
        "cpu_loading": "Température CPU : chargement…",
        "cpu_ok": "Température CPU ✓",
        "cpu_repair": "● Réparer la température CPU…",
        "check_update": "v{v} · Rechercher une mise à jour",
        "script": "v{v} (script)",
        "quit": "Quitter",
        "searching": "Recherche…",
        "cant_check": "v{v} · Impossible de vérifier",
        "update_to": "● Mettre à jour vers {tag}",
        "up_to_date": "v{v} · À jour ✓",
        "downloading": "Téléchargement…",
        "updating": "mise à jour…",
        "update_failed": "● Échec, réessayer la mise à jour",
        "city_title": "Ville",
        "city_hint": "Tape une ville puis Entrée",
        "net_error": "Erreur réseau",
        "no_result": "Aucun résultat",
    },
}


def t(key, **kw):
    text = STRINGS.get(LANG, STRINGS["en"]).get(key) or STRINGS["en"][key]
    return text.format(**kw) if kw else text


# ---------- crisp rendering on 125 %, 150 %… displays ----------
def _set_dpi_awareness():
    """Per-monitor DPI awareness (v2): Windows never stretches the window (no blur), and
    the real scale of the screen is always known, even if it changes after start-up."""
    if not IS_WIN:
        return
    import ctypes
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_set_dpi_awareness()
FORCED_SCALE = os.environ.get("SIMPLEOVERLAY_SCALE")


def system_scale():
    scale = 1.0
    if IS_WIN:
        import ctypes
        try:
            scale = ctypes.windll.user32.GetDpiForSystem() / 96
        except Exception:
            try:
                hdc = ctypes.windll.user32.GetDC(0)
                scale = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88) / 96  # LOGPIXELSX
                ctypes.windll.user32.ReleaseDC(0, hdc)
            except Exception:
                pass
    if FORCED_SCALE:
        try:
            scale = float(FORCED_SCALE)
        except ValueError:
            pass
    return max(1.0, scale)


def window_scale(widget):
    """Scale of the screen the window is currently on (None if unknown)."""
    if FORCED_SCALE or not IS_WIN:
        return None
    try:
        import ctypes
        u32 = ctypes.windll.user32
        hwnd = u32.GetAncestor(widget.winfo_id(), 2)  # GA_ROOT
        dpi = u32.GetDpiForWindow(hwnd)
        return max(1.0, dpi / 96) if dpi else None
    except Exception:
        return None


SCALE = system_scale()


def sc(v):
    """Converts a logical size (at 100 %) to physical pixels."""
    return int(round(v * SCALE))


# NVIDIA GPU through NVML (lightweight, no nvidia-smi calls)
try:
    import pynvml
    pynvml.nvmlInit()
    NV = pynvml.nvmlDeviceGetHandleByIndex(0)
except Exception:
    NV = None

HOME = os.path.expanduser("~")
CFG_PATH = os.path.join(HOME, ".mini_overlay.json")
LOG_PATH = os.path.join(HOME, ".simpleoverlay.log")
DEFAULT_CFG = {"city": "Paris", "lat": 48.8566, "lon": 2.3522, "x": 40, "y": 40, "alpha": 0.9}

# Palette: deep black and neutral greys, colours only for warnings
BG, FG, MUTED, TRACK, LINE = "#0b0b0b", "#ececec", "#8a8a8a", "#1e1e1e", "#222222"
OK, WARN, HOT = "#b5b5b5", "#d9a45b", "#e06c6c"
KEY = "#010101"  # colour made transparent (rounded corners on Windows)
FONT = "Segoe UI" if IS_WIN else ("SF Pro Text" if IS_MAC else "Helvetica")
APP_FONT = "Inter"  # bundled font (fonts/ folder, SIL Open Font License)


def fonts_dir():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "fonts")


def register_fonts():
    """Makes the bundled font available to this process only (nothing is installed)."""
    if not IS_WIN:
        return
    import ctypes
    import glob
    for path in glob.glob(os.path.join(fonts_dir(), "*.ttf")):
        try:
            ctypes.windll.gdi32.AddFontResourceExW(path, 0x10, 0)  # FR_PRIVATE
        except Exception:
            pass


register_fonts()

# Logical dimensions (at 100 %), converted with sc()
LW, LPAD = 176, 13
LHEAD = 44        # header height (temperatures)
LROW = 18         # height of a GPU / VRAM / CPU / RAM row
LH = LPAD + LHEAD + 8 + LROW * 4 + 6
W, H, PAD = sc(LW), sc(LH), sc(LPAD)


def set_scale(scale):
    """Changes the scale factor and recomputes the overlay dimensions."""
    global SCALE, W, H, PAD
    SCALE = scale
    W, H, PAD = sc(LW), sc(LH), sc(LPAD)
CITY_PX_MAX, CITY_PX_MIN = 11, 9   # city name size (pixels at 100 %)
TEMP_PX_MAX, TEMP_PX_MIN = 15, 12  # header temperature size (pixels at 100 %)

WEATHER_EVERY_MS = 10 * 60 * 1000
WEATHER_RETRY_MS = 60 * 1000
STATS_EVERY_MS = 1000


# ---------- helpers ----------
def log(msg):
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 100_000:
            os.remove(LOG_PATH)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} v{VERSION} {msg}\n")
    except Exception:
        pass


def load_cfg():
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            return {**DEFAULT_CFG, **json.load(f)}
    except Exception:
        return dict(DEFAULT_CFG)


def save_cfg(cfg):
    try:
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except Exception:
        pass


def _ssl_context():
    """Windows certificates + bundled ones (certifi): avoids HTTPS failures when a root
    authority (e.g. Let's Encrypt) is missing from the Windows certificate store."""
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


SSL_CTX = _ssl_context()


def http_get(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": f"SimpleOverlay/{VERSION}"})
    return urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX)


def get_json(url):
    with http_get(url) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------- start with Windows (registry Run key, no admin rights) ----------
APP_NAME = "SimpleOverlay"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_cmd():
    if FROZEN:  # .exe build
        return f'"{sys.executable}"'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    return f'"{exe}" "{os.path.abspath(__file__)}"'


def autostart_get():
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def autostart_set(on):
    if not IS_WIN:
        return
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, launch_cmd())
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except OSError:
                    pass
    except OSError:
        pass


def single_instance():
    """Prevents two overlays from running at the same time (reserved local port)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 47653))
        return s
    except OSError:
        return None


# ---------- self-update (.exe build only) ----------
def parse_version(v):
    try:
        return tuple(int(x) for x in v.strip().lstrip("vV").split("."))
    except Exception:
        return (0,)


def latest_release():
    """Returns (version, exe url) of the latest GitHub release, or None."""
    d = get_json(f"https://api.github.com/repos/{REPO}/releases/latest")
    for a in d.get("assets", []):
        if a.get("name") == EXE_NAME:
            return d.get("tag_name", ""), a["browser_download_url"]
    return None


def download_update(url):
    """Downloads the new exe next to the current one and returns its path."""
    new = sys.executable + ".new"
    with http_get(url, timeout=60) as r, open(new, "wb") as f:
        while True:
            chunk = r.read(64 * 1024)
            if not chunk:
                break
            f.write(chunk)
    with open(new, "rb") as f:
        if f.read(2) != b"MZ" or os.path.getsize(new) < 1_000_000:
            os.remove(new)
            raise RuntimeError("invalid download")
    return new


def swap_and_restart(new):
    """Windows allows renaming a running exe: move it aside, put the new one in its
    place, then start the new one."""
    import subprocess
    exe = sys.executable
    old = exe + ".old"
    try:
        if os.path.exists(old):
            os.remove(old)
    except OSError:
        pass
    os.replace(exe, old)
    os.replace(new, exe)
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    subprocess.Popen([exe], env=env, close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


def cleanup_old_exe():
    if FROZEN:
        for suffix in (".old", ".new"):
            try:
                os.remove(sys.executable + suffix)
            except OSError:
                pass


def gpu_stats():
    """Returns (temperature °C, usage %, VRAM used %), None when unavailable."""
    if NV is not None:
        try:
            t_ = pynvml.nvmlDeviceGetTemperature(NV, pynvml.NVML_TEMPERATURE_GPU)
            u = pynvml.nvmlDeviceGetUtilizationRates(NV).gpu
            mem = pynvml.nvmlDeviceGetMemoryInfo(NV)
            vr = 100.0 * mem.used / mem.total if mem.total else None
            return t_, u, vr
        except Exception:
            pass
    # Linux fallback (AMD / nouveau)
    t_ = u = None
    try:
        sensors = psutil.sensors_temperatures()
        for k in ("amdgpu", "radeon", "nouveau"):
            if sensors.get(k):
                t_ = sensors[k][0].current
                break
    except Exception:
        pass
    try:
        with open("/sys/class/drm/card0/device/gpu_busy_percent") as f:
            u = int(f.read().strip())
    except Exception:
        pass
    vr = None
    try:
        dev = "/sys/class/drm/card0/device/"
        with open(dev + "mem_info_vram_used") as f1, open(dev + "mem_info_vram_total") as f2:
            vr = 100.0 * int(f1.read()) / int(f2.read())
    except Exception:
        pass
    return t_, u, vr


# ---------- built-in sensors (LibreHardwareMonitorLib + PawnIO driver) ----------
# The CPU temperature can only be read with a driver (PawnIO) and administrator rights.
# Setup (--setup, a single Windows prompt) installs PawnIO and creates two scheduled tasks
# that start the overlay with admin rights, without any confirmation afterwards.
TASK_RUN = "SimpleOverlay"            # on-demand start (shortcut, restart)
TASK_LOGON = "SimpleOverlay Startup"  # start at logon
SETUP_MARK = os.path.join(HOME, ".simpleoverlay.setup")
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW

_CPU_IDS = ("/intelcpu/", "/amdcpu/", "/cpu/")
_GPU_IDS = ("/gpu-nvidia/", "/gpu-amd/", "/gpu-intel/", "/atigpu/", "/nvidiagpu/")
_CPU_NAMES = ("cpu package", "core (tctl/tdie)", "tdie", "tctl", "core average", "cpu cores", "package")
_GPU_NAMES = ("gpu core", "gpu hot spot")


def _pick(sensors, ids, names):
    """Picks the best value among (identifier, name, value) tuples for one component."""
    cand = [(n.lower(), v) for i, n, v in sensors
            if any(k in i.lower() for k in ids) and v and v > 0
            and "distance" not in n.lower() and "tjmax" not in n.lower()]
    for want in names:
        for n, v in cand:
            if n == want:
                return v
    return max((v for _, v in cand), default=None)


def is_admin():
    if not IS_WIN:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _run(args, **kw):
    import subprocess
    return subprocess.run(args, capture_output=True, text=True,
                          creationflags=NO_WINDOW if IS_WIN else 0, **kw)


def task_exists(name=TASK_RUN):
    if not IS_WIN:
        return False
    try:
        return _run(["schtasks", "/query", "/tn", name]).returncode == 0
    except Exception:
        return False


def task_enabled(name=TASK_LOGON):
    try:
        r = _run(["schtasks", "/query", "/tn", name, "/xml"])
        return r.returncode == 0 and "<Enabled>false</Enabled>" not in r.stdout
    except Exception:
        return False


def task_set_enabled(on, name=TASK_LOGON):
    try:
        _run(["schtasks", "/change", "/tn", name, "/ENABLE" if on else "/DISABLE"])
    except Exception:
        pass


def task_run(name=TASK_RUN):
    try:
        return _run(["schtasks", "/run", "/tn", name]).returncode == 0
    except Exception:
        return False


def current_user():
    dom, user = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    return f"{dom}\\{user}" if dom else user


def pawnio_version():
    """Installed PawnIO version (tuple), or None."""
    if not IS_WIN:
        return None
    import winreg
    for flag in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO",
                                0, winreg.KEY_READ | flag) as k:
                return parse_version(str(winreg.QueryValueEx(k, "DisplayVersion")[0]))
        except OSError:
            pass
    return None


def install_pawnio():
    """Downloads and silently installs the PawnIO driver (signed, open source)."""
    import tempfile
    url = "https://github.com/namazso/PawnIO.Setup/releases/download/2.2.0/PawnIO_setup.exe"
    try:
        d = get_json("https://api.github.com/repos/namazso/PawnIO.Setup/releases/latest")
        for a in d.get("assets", []):
            if a.get("name", "").lower().endswith(".exe"):
                url = a["browser_download_url"]
                break
    except Exception as ex:
        log(f"setup: using the default PawnIO version ({ex})")
    path = os.path.join(tempfile.gettempdir(), "PawnIO_setup.exe")
    with http_get(url, timeout=120) as r, open(path, "wb") as f:
        f.write(r.read())
    with open(path, "rb") as f:
        if f.read(2) != b"MZ":
            raise RuntimeError("invalid PawnIO installer")
    code = _run([path, "-install", "-silent"], timeout=300).returncode
    log(f"setup: PawnIO installed (code {code})")
    if code not in (0, 3010):
        raise RuntimeError(f"PawnIO installation: code {code}")


def register_tasks(exe, user):
    def q(s):
        return "'" + s.replace("'", "''") + "'"
    ps = f"""
$ErrorActionPreference = 'Stop'
$a = New-ScheduledTaskAction -Execute {q(exe)}
$p = New-ScheduledTaskPrincipal -UserId {q(user)} -LogonType Interactive -RunLevel Highest
$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries `
     -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -Priority 5
Register-ScheduledTask -TaskName {q(TASK_RUN)} -Action $a -Principal $p -Settings $s -Force | Out-Null
$t = New-ScheduledTaskTrigger -AtLogOn -User {q(user)}
Register-ScheduledTask -TaskName {q(TASK_LOGON)} -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null
"""
    r = _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], timeout=120)
    if r.returncode != 0:
        raise RuntimeError("creating the scheduled tasks: " + (r.stderr or r.stdout).strip()[-300:])
    log("setup: scheduled tasks created")


def run_setup(user, start=True):
    """--setup mode (runs with admin rights): PawnIO + scheduled tasks, then restart."""
    try:
        if not is_admin():
            raise RuntimeError("administrator rights denied")
        v = pawnio_version()
        if v is None or v < (2,):
            install_pawnio()
        else:
            log(f"setup: PawnIO already installed ({'.'.join(map(str, v))})")
        register_tasks(sys.executable, user)
        autostart_set(False)  # the old start method (Run key, no admin) is replaced by the task
        result = "ok"
    except Exception as ex:
        log(f"setup: failed: {type(ex).__name__}: {ex}")
        result = f"error: {ex}"
    try:
        with open(SETUP_MARK, "w", encoding="utf-8") as f:
            f.write(result)
    except Exception:
        pass
    if result == "ok" and start:
        time.sleep(1.0)  # let the current instance exit
        task_run()
    return result == "ok"


def launch_setup():
    """Restarts the exe with admin rights (one Windows prompt) in --setup mode."""
    import ctypes
    try:
        os.remove(SETUP_MARK)
    except OSError:
        pass
    args = f'--setup --user "{current_user()}"'
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, args, None, 0)
    return rc > 32


class HWSensors:
    """Reads the sensors in the background with the bundled LibreHardwareMonitorLib.
    Needs admin rights and the PawnIO driver; does nothing otherwise."""

    def __init__(self, want_gpu):
        self.cpu = None
        self.gpu = None
        self.gpu_load = None
        self.gpu_vram = None
        self.want_gpu = want_gpu
        self.state = "off"  # off | loading | ok | nodata | error
        if IS_WIN and is_admin():
            self.state = "loading"
            threading.Thread(target=self._loop, daemon=True).start()

    @staticmethod
    def lib_dir():
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, "lhm")

    def open(self):
        d = self.lib_dir()
        if d not in sys.path:
            sys.path.append(d)
        import clr  # pythonnet (.NET Framework, shipped with Windows 10/11)
        clr.AddReference("LibreHardwareMonitorLib")
        from LibreHardwareMonitor.Hardware import Computer, SensorType
        c = Computer()
        c.IsCpuEnabled = True
        c.IsGpuEnabled = self.want_gpu
        c.Open()
        return c, SensorType

    def read(self, c, st):
        temps, loads = [], []

        def collect(hw):
            hw.Update()
            for s in hw.Sensors:
                v = s.Value
                if v is None:
                    continue
                item = (str(s.Identifier), str(s.Name), float(v))
                if s.SensorType == st.Temperature:
                    temps.append(item)
                elif s.SensorType == st.Load:
                    loads.append(item)
            for sub in hw.SubHardware:
                collect(sub)

        for hw in c.Hardware:
            collect(hw)
        return temps, loads

    def _loop(self):
        try:
            c, st = self.open()
        except Exception as ex:
            log(f"sensors: cannot load: {type(ex).__name__}: {ex}")
            self.state = "error"
            return
        fails = 0
        while True:
            try:
                temps, loads = self.read(c, st)
                self.cpu = _pick(temps, _CPU_IDS, _CPU_NAMES)
                self.gpu = _pick(temps, _GPU_IDS, _GPU_NAMES)
                self.gpu_load = _pick(loads, _GPU_IDS, ("gpu core", "d3d 3d"))
                self.gpu_vram = next((v for i, n, v in loads
                                      if any(k in i.lower() for k in _GPU_IDS)
                                      and n.lower() in ("gpu memory", "d3d dedicated memory")), None)
                self.state = "ok" if self.cpu else "nodata"
                fails = 0
            except Exception as ex:
                fails += 1
                if fails == 1:
                    log(f"sensors: read error: {type(ex).__name__}: {ex}")
                if fails > 5:
                    self.state = "error"
            time.sleep(2)


def self_test(path):
    """--selftest: checks that the sensor library, Tk and the font load (used by the CI)."""
    out = [f"admin={is_admin()} pawnio={pawnio_version()}"]
    try:
        hs = HWSensors.__new__(HWSensors)
        hs.want_gpu = True
        c, st = hs.open()
        temps, loads = hs.read(c, st)
        out.append(f"temps={temps[:6]}")
        out.append(f"cpu={_pick(temps, _CPU_IDS, _CPU_NAMES)}")
        out.append("LOAD OK")
    except Exception as ex:
        import traceback
        out.append("LOAD FAIL " + "".join(traceback.format_exception(ex)))
    try:
        import tkinter
        tkinter.Tcl().eval("info patchlevel")
        out.append("TK OK")
        root = tkinter.Tk()
        fams = tkinter.font.families(root)
        out.append("FONT OK" if APP_FONT in fams else f"FONT FAIL ({len(fams)} families)")
        root.destroy()
    except Exception as ex:
        out.append(f"TK FAIL {ex}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def wx_icon(code):
    if code is None:
        return ""
    if code == 0:
        return "☀"
    if code in (1, 2):
        return "⛅"
    if code in (45, 48):
        return "≡"
    if 51 <= code <= 67 or 80 <= code <= 82:
        return "☂"
    if 71 <= code <= 77 or code in (85, 86):
        return "❄"
    if code >= 95:
        return "ϟ"
    return "☁"


def level_color(v, warn, hot, base=OK):
    if v is None:
        return MUTED
    return base if v < warn else WARN if v < hot else HOT


# ---------- overlay ----------
class Overlay:
    def __init__(self):
        self.cfg = load_cfg()
        # "Scheduled task" mode: the overlay runs with admin rights (CPU temperature)
        self.task_mode = IS_WIN and FROZEN and task_exists()
        self.setup_state = None  # None | running | failed
        # First run: enable start with Windows; afterwards keep the path up to date
        if self.task_mode:
            autostart_set(False)  # start-up goes through the scheduled task
        elif IS_WIN:
            if not self.cfg.get("autostart_init"):
                autostart_set(True)
                self.cfg["autostart_init"] = True
                save_cfg(self.cfg)
            elif autostart_get():
                autostart_set(True)
        self.q = queue.Queue()  # results from network threads -> UI thread
        self.picker = None
        self._picker_t = 0.0
        self.city_full = self.cfg["city"]
        self.update = None     # (version, url) when an update is available
        self.updating = False
        self._wx_job = None
        cleanup_old_exe()
        self.hw = HWSensors(want_gpu=NV is None)
        self._hw_label = None

        r = self.root = tk.Tk()
        r.title("SimpleOverlay")
        global FONT
        if APP_FONT in tkfont.families(r):
            FONT = APP_FONT
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.attributes("-alpha", self.cfg["alpha"])
        if IS_WIN:
            r.config(bg=KEY)
            r.attributes("-transparentcolor", KEY)
        self.c = None
        self.menu = None
        self._wx_state = ("…", FG)
        self._upd_label = None
        self._pos = (int(self.cfg["x"]), int(self.cfg["y"]))
        self.build_ui()
        r.update_idletasks()
        self.check_dpi()  # the screen may not use the system scale

        psutil.cpu_percent(None)  # primes the non-blocking measurement
        self.tick()
        self.refresh_weather()
        if FROZEN:
            r.after(8000, self._update_loop)
        r.after(3000, self._dpi_loop)
        self._poll()

    # --- user interface (rebuilt when the display scale changes) ---
    def build_ui(self):
        r = self.root
        # Font sizes (in points) follow the display scale
        r.tk.call("tk", "scaling", SCALE * 96 / 72)
        self.close_picker(force=True)
        if self.c is not None:
            self.c.destroy()
        if self.menu is not None:
            self.menu.destroy()

        # Keep the window on screen if the saved position is off-screen
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        x = min(max(0, self._pos[0]), sw - W)
        y = min(max(0, self._pos[1]), sh - H)
        r.geometry(f"{W}x{H}+{x}+{y}")
        canvas_bg = KEY if IS_WIN else BG

        c = self.c = tk.Canvas(r, width=W, height=H, bg=canvas_bg, highlightthickness=0, bd=0)
        c.pack()
        self._round_rect(0, 0, W - 1, H - 1, sc(12), fill=BG, outline=LINE)

        f_lbl = (FONT, 8)
        f_val = (FONT, 9, "bold")
        self.sub_font = tkfont.Font(family=FONT, size=8)
        # City font: pixel size, reduced according to the length of the name
        self.city_font = tkfont.Font(family=FONT, size=-sc(CITY_PX_MAX))

        # --- Header: GPU and CPU temperatures (left), weather (right) ---
        y_big, y_sub = PAD + sc(10), PAD + sc(29)
        x_gpu = PAD
        gap = sc(8)
        # Largest font size for which "88°C 88°C … ☀ -88°C" fits without widening the overlay
        self.big_font = tkfont.Font(family=FONT, size=-sc(TEMP_PX_MAX), weight="bold")
        for px in range(TEMP_PX_MAX, TEMP_PX_MIN - 1, -1):
            self.big_font.configure(size=-sc(px))
            slot = self.big_font.measure("88°C")
            if x_gpu + 2 * slot + gap + sc(10) + self.big_font.measure("☀ -88°C") <= W - PAD:
                break
        x_cpu = x_gpu + slot + gap
        f_big = self.big_font
        self.gtemp_txt = c.create_text(x_gpu, y_big, text="—", anchor="w", fill=FG, font=f_big)
        self.ctemp_txt = c.create_text(x_cpu, y_big, text="—", anchor="w", fill=MUTED, font=f_big)
        self.gsub_txt = c.create_text(x_gpu + 1, y_sub, text="GPU", anchor="w", fill=MUTED, font=self.sub_font)
        self.csub_txt = c.create_text(x_cpu + 1, y_sub, text="CPU", anchor="w", fill=MUTED, font=self.sub_font)

        self.wx_txt = c.create_text(W - PAD, y_big, text="…", anchor="e", fill=FG, font=f_big)
        self.city_txt = c.create_text(W - PAD, y_sub, text=self.city_full, anchor="e",
                                      fill=MUTED, font=self.city_font)
        self._fit_city()

        ys = PAD + sc(LHEAD)
        c.create_line(PAD, ys, W - PAD, ys, fill=LINE)

        # --- GPU / VRAM / CPU / RAM rows: label, thin bar, value ---
        self.rows = {}
        bx0 = PAD + sc(40)
        bx1 = bx0 + sc(72)  # short bars
        bh = max(1, sc(1.5))
        for i, (key, label) in enumerate(
                [("gpu", "GPU"), ("vram", "VRAM"), ("cpu", "CPU"), ("ram", "RAM")]):
            yy = ys + sc(8) + sc(LROW) * i + sc(LROW) // 2
            c.create_text(PAD, yy, text=label, anchor="w", fill=MUTED, font=f_lbl)
            c.create_rectangle(bx0, yy - bh, bx1, yy + bh, fill=TRACK, width=0)
            bar = c.create_rectangle(bx0, yy - bh, bx0, yy + bh, fill=OK, width=0)
            val = c.create_text(W - PAD, yy, text="—", anchor="e", fill=FG, font=f_val)
            self.rows[key] = (bar, val, bx0, bx1, yy, bh, None)

        # Small discreet dot when an update is available
        d = sc(2.5)
        cx, cy = W - sc(8), sc(8)
        self.dot = c.create_oval(cx - d, cy - d, cx + d, cy + d, fill=OK, width=0, state="hidden")

        # Interactions
        c.bind("<ButtonPress-1>", self._drag_start)
        c.bind("<B1-Motion>", self._drag_move)
        c.bind("<ButtonRelease-1>", self._drag_end)
        c.bind("<Button-2>" if IS_MAC else "<Button-3>", self._show_menu)
        for item in (self.city_txt, self.wx_txt):
            c.tag_bind(item, "<Double-Button-1>", lambda e: self.pick_city())

        mkw = dict(tearoff=0, bg=BG, fg=FG, activebackground=TRACK, activeforeground=FG, bd=0, font=(FONT, 9))
        m = self.menu = tk.Menu(r, **mkw)
        m.add_command(label=t("change_city"), command=self.pick_city)
        m.add_command(label=t("refresh_weather"), command=self.refresh_weather)
        m.add_command(label=t("refresh_display"), command=self.refresh_display)
        op = tk.Menu(m, **mkw)
        for a in (1.0, 0.9, 0.75, 0.6, 0.45):
            op.add_command(label=f"{int(a * 100)}%", command=lambda a=a: self.set_alpha(a))
        m.add_cascade(label=t("opacity"), menu=op)
        if IS_WIN:
            init = task_enabled() if self.task_mode else autostart_get()
            self.auto_var = tk.BooleanVar(value=init)
            m.add_checkbutton(
                label=t("autostart"), variable=self.auto_var,
                command=self.toggle_autostart, selectcolor=FG,
            )
        if IS_WIN and FROZEN:
            m.add_command(label=t("cpu_loading"), command=self.on_cpu_click)
            self.hw_idx = m.index("end")
        m.add_separator()
        if FROZEN:
            m.add_command(label=t("check_update", v=VERSION), command=self.on_update_click)
        else:
            m.add_command(label=t("script", v=VERSION), state="disabled")
        self.upd_idx = m.index("end")
        m.add_command(label=t("quit"), command=self.quit)

        # Restore the current state on the new widgets
        self._last = {}
        self._hw_label = None
        self._text(self.wx_txt, *self._wx_state)
        if self._upd_label:
            self._set_upd_label(*self._upd_label)
        if self.update:
            c.itemconfig(self.dot, state="normal")

    def refresh_display(self):
        """Redraws the whole overlay at the current scale of its screen (fixes blur)."""
        self._pos = (self.root.winfo_x(), self.root.winfo_y())
        s = window_scale(self.root) or system_scale()
        log(f"display refresh: scale {SCALE:g} -> {s:g}")
        set_scale(s)
        self.build_ui()
        self.update_values()

    def check_dpi(self):
        s = window_scale(self.root)
        if s and abs(s - SCALE) > 0.01:
            log(f"display scale changed: {SCALE:g} -> {s:g}")
            self._pos = (self.root.winfo_x(), self.root.winfo_y())
            set_scale(s)
            self.build_ui()
            self.update_values()

    def _dpi_loop(self):
        self.check_dpi()
        self.root.after(3000, self._dpi_loop)

    # --- drawing ---
    def _round_rect(self, x0, y0, x1, y1, rad, **kw):
        p = [x0 + rad, y0, x1 - rad, y0, x1, y0, x1, y0 + rad, x1, y1 - rad, x1, y1,
             x1 - rad, y1, x0 + rad, y1, x0, y1, x0, y1 - rad, x0, y0 + rad, x0, y0]
        return self.c.create_polygon(p, smooth=True, **kw)

    def _fit_city(self):
        """Shrinks the city font according to the name length, then truncates it (…)
        as a last resort so it never touches the CPU label."""
        left = self.c.bbox(self.csub_txt)[2] + sc(10)
        avail = (W - PAD) - left
        name = self.city_full
        f = self.city_font
        for px in range(CITY_PX_MAX, CITY_PX_MIN - 1, -1):
            f.configure(size=-sc(px))
            if f.measure(name) <= avail:
                break
        if f.measure(name) > avail:
            while name and f.measure(name.rstrip() + "…") > avail:
                name = name[:-1]
            name = name.rstrip() + "…"
        self.c.itemconfig(self.city_txt, text=name)

    def _text(self, item, text, color=None):
        """Updates a text item only when it changed (avoids useless redraws)."""
        key = (item, text, color)
        if self._last.get(item) == key:
            return
        self._last[item] = key
        kw = {"text": text}
        if color:
            kw["fill"] = color
        self.c.itemconfig(item, **kw)

    def _set_row(self, key, v, text, warn=70, hot=90):
        bar, val, x0, x1, yy, bh, last = self.rows[key]
        if text == last:
            return
        frac = 0 if v is None else max(0.0, min(1.0, v / 100))
        self.c.coords(bar, x0, yy - bh, x0 + (x1 - x0) * frac, yy + bh)
        self.c.itemconfig(bar, fill=level_color(v, warn, hot) if v is not None else TRACK)
        self.c.itemconfig(val, text=text)
        self.rows[key] = (bar, val, x0, x1, yy, bh, text)

    # --- CPU temperature: setup (admin rights, once) ---
    def toggle_autostart(self):
        on = self.auto_var.get()
        if self.task_mode:
            task_set_enabled(on)
        else:
            autostart_set(on)

    def _cpu_menu_label(self):
        if self.setup_state == "running":
            return t("cpu_running"), False
        if self.setup_state == "failed":
            return t("cpu_failed"), True
        st = self.hw.state
        if st == "off":
            return t("cpu_enable"), True
        if st == "loading":
            return t("cpu_loading"), False
        if st == "ok":
            return t("cpu_ok"), False
        return t("cpu_repair"), True

    def on_cpu_click(self):
        if self.setup_state == "running":
            return
        if not launch_setup():
            log("setup: administrator prompt declined or unavailable")
            self.setup_state = "failed"
            return
        self.setup_state = "running"
        self._setup_t0 = time.time()
        self.root.after(500, self._wait_setup)

    def _wait_setup(self):
        try:
            with open(SETUP_MARK, encoding="utf-8") as f:
                result = f.read().strip()
        except OSError:
            result = None
        if result is None:
            if time.time() - self._setup_t0 < 600:
                self.root.after(500, self._wait_setup)
            else:
                self.setup_state = "failed"
            return
        if result == "ok":
            # the new instance (with admin rights) is started by the scheduled task
            release_lock()
            self.quit()
        else:
            self.setup_state = "failed"

    # --- loops ---
    def tick(self):
        self.update_values()
        self.root.after(STATS_EVERY_MS, self.tick)

    def update_values(self):
        gt, u, vr = gpu_stats()
        if gt is None:
            gt = self.hw.gpu  # non-NVIDIA GPU: built-in sensors
        if u is None:
            u = self.hw.gpu_load
        if vr is None:
            vr = self.hw.gpu_vram
        ct = self.hw.cpu
        cpu = psutil.cpu_percent(None)
        ram = psutil.virtual_memory().percent
        self._text(self.gtemp_txt, f"{gt:.0f}°C" if gt is not None else "—",
                   level_color(gt, 72, 84, base=FG) if gt is not None else MUTED)
        self._text(self.ctemp_txt, f"{ct:.0f}°C" if ct is not None else "—",
                   level_color(ct, 80, 90, base=FG) if ct is not None else MUTED)
        if IS_WIN and FROZEN:
            label = self._cpu_menu_label()
            if label != self._hw_label:
                self._hw_label = label
                self.menu.entryconfig(self.hw_idx, label=label[0],
                                      state="normal" if label[1] else "disabled")
        self._set_row("gpu", u, f"{u:.0f}%" if u is not None else "—")
        self._set_row("vram", vr, f"{vr:.0f}%" if vr is not None else "—", warn=85, hot=95)
        self._set_row("cpu", cpu, f"{cpu:.0f}%")
        self._set_row("ram", ram, f"{ram:.0f}%", warn=75, hot=90)

    def _poll(self):
        try:
            while True:
                self.q.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(150, self._poll)

    def refresh_weather(self):
        if self._wx_job is not None:
            self.root.after_cancel(self._wx_job)
        lat, lon = self.cfg["lat"], self.cfg["lon"]

        def work():
            try:
                d = get_json(
                    "https://api.open-meteo.com/v1/forecast?"
                    + urllib.parse.urlencode({
                        "latitude": lat, "longitude": lon,
                        "current": "temperature_2m,weather_code", "timezone": "auto",
                    })
                )
                cur = d["current"]
                icon = wx_icon(cur.get("weather_code"))
                txt, ok = f"{icon} {cur['temperature_2m']:.0f}°C".strip(), True
            except Exception as ex:
                log(f"weather: {type(ex).__name__}: {ex}")
                txt, ok = "—", False

            def done():
                self._wx_state = (txt, FG if ok else MUTED)
                self._text(self.wx_txt, *self._wx_state)
                self._wx_job = self.root.after(
                    WEATHER_EVERY_MS if ok else WEATHER_RETRY_MS, self.refresh_weather
                )

            self.q.put(done)

        threading.Thread(target=work, daemon=True).start()

    # --- updates ---
    def _set_upd_label(self, text, enabled=True):
        self._upd_label = (text, enabled)
        self.menu.entryconfig(self.upd_idx, label=text, state="normal" if enabled else "disabled")

    def _update_loop(self):
        self.check_update()
        self.root.after(UPDATE_EVERY_MS, self._update_loop)

    def check_update(self, manual=False):
        if self.updating:
            return
        if manual:
            self._set_upd_label(t("searching"), enabled=False)

        def work():
            try:
                rel = latest_release()
            except Exception as ex:
                log(f"update check: {type(ex).__name__}: {ex}")
                rel = False

            def done():
                if rel is False:
                    if manual:
                        self._set_upd_label(t("cant_check", v=VERSION))
                    return
                if rel and parse_version(rel[0]) > parse_version(VERSION):
                    self.update = rel
                    self.c.itemconfig(self.dot, state="normal")
                    self._set_upd_label(t("update_to", tag=rel[0]))
                else:
                    self.update = None
                    self.c.itemconfig(self.dot, state="hidden")
                    if manual:
                        self._set_upd_label(t("up_to_date", v=VERSION))

            self.q.put(done)

        threading.Thread(target=work, daemon=True).start()

    def on_update_click(self):
        if self.update:
            self.apply_update()
        else:
            self.check_update(manual=True)

    def apply_update(self):
        if self.updating or not self.update:
            return
        self.updating = True
        self._set_upd_label(t("downloading"), enabled=False)
        self.c.itemconfig(self.city_txt, text=t("updating"))
        url = self.update[1]

        def fail(ex):
            log(f"update install: {type(ex).__name__}: {ex}")
            self.updating = False
            self._set_upd_label(t("update_failed"))
            self._fit_city()

        def work():
            try:
                new = download_update(url)
                err = None
            except Exception as ex:
                new, err = None, ex

            def done():
                if err is not None:
                    fail(err)
                    return
                try:
                    release_lock()
                    swap_and_restart(new)
                except Exception as ex:
                    fail(ex)
                    return
                self.quit()

            self.q.put(done)

        threading.Thread(target=work, daemon=True).start()

    # --- moving ---
    def _drag_start(self, e):
        self._dx, self._dy = e.x, e.y
        self.close_picker()  # clicking the overlay closes the city picker

    def close_picker(self, force=False):
        p = self.picker
        if p is None or not p.winfo_exists():
            return
        # ignore the click that just opened the picker (double-click)
        if not force and time.time() - self._picker_t < 0.4:
            return
        p.destroy()
        self.picker = None

    def _drag_move(self, e):
        x = self.root.winfo_x() + e.x - self._dx
        y = self.root.winfo_y() + e.y - self._dy
        self.root.geometry(f"+{x}+{y}")

    def _drag_end(self, _e):
        self.cfg["x"], self.cfg["y"] = self.root.winfo_x(), self.root.winfo_y()
        self._pos = (self.cfg["x"], self.cfg["y"])
        save_cfg(self.cfg)
        self.check_dpi()  # moved to a screen with another scale?

    # --- menu / settings ---
    def _show_menu(self, e):
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def set_alpha(self, a):
        self.cfg["alpha"] = a
        self.root.attributes("-alpha", a)
        save_cfg(self.cfg)

    def quit(self):
        try:
            if NV is not None:
                pynvml.nvmlShutdown()
        except Exception:
            pass
        self.root.destroy()

    # --- city picker ---
    def pick_city(self):
        if self.picker is not None and self.picker.winfo_exists():
            self.picker.lift()
            return
        self._picker_t = time.time()
        p = self.picker = tk.Toplevel(self.root)
        p.title(t("city_title"))
        if IS_WIN:
            p.overrideredirect(True)
        p.attributes("-topmost", True)
        p.config(bg=BG, highlightthickness=1, highlightbackground=LINE)
        ph = sc(170)
        px = self.root.winfo_x()
        py = self.root.winfo_y() + H + sc(6)
        if py + ph > self.root.winfo_screenheight():
            py = self.root.winfo_y() - ph - sc(6)
        p.geometry(f"{W}x{ph}+{px}+{py}")

        top = tk.Frame(p, bg=BG)
        top.pack(fill="x", padx=sc(8), pady=(sc(8), sc(4)))
        e = tk.Entry(top, bg=TRACK, fg=FG, insertbackground=FG, relief="flat", font=(FONT, 9))
        e.pack(side="left", fill="x", expand=True, ipady=sc(4))
        close = tk.Label(top, text="✕", bg=BG, fg=MUTED, font=(FONT, 9), cursor="hand2", padx=sc(6))
        close.pack(side="right")
        close.bind("<Button-1>", lambda _: p.destroy())

        lb = tk.Listbox(
            p, bg=BG, fg=FG, selectbackground=TRACK, selectforeground=FG, relief="flat",
            highlightthickness=0, bd=0, font=(FONT, 9), activestyle="none",
        )
        lb.pack(fill="both", expand=True, padx=sc(8), pady=(0, sc(8)))
        lb.insert("end", t("city_hint"))
        results = []

        def search(_=None):
            query = e.get().strip()
            if not query:
                return
            lb.delete(0, "end")
            lb.insert("end", t("searching"))

            def work():
                try:
                    d = get_json(
                        "https://geocoding-api.open-meteo.com/v1/search?"
                        + urllib.parse.urlencode({"name": query, "count": 6, "language": LANG, "format": "json"})
                    )
                    res = d.get("results") or []
                except Exception as ex:
                    log(f"city search: {type(ex).__name__}: {ex}")
                    res = None

                def show():
                    if not p.winfo_exists():
                        return
                    lb.delete(0, "end")
                    results.clear()
                    if res is None:
                        lb.insert("end", t("net_error"))
                    elif not res:
                        lb.insert("end", t("no_result"))
                    else:
                        for item in res:
                            results.append(item)
                            parts = (item.get("name"), item.get("admin1"), item.get("country_code"))
                            lb.insert("end", ", ".join(x for x in parts if x))
                        lb.selection_set(0)

                self.q.put(show)

            threading.Thread(target=work, daemon=True).start()

        def choose(_=None):
            sel = lb.curselection()
            if not sel or sel[0] >= len(results):
                return
            item = results[sel[0]]
            self.cfg.update(city=item["name"], lat=item["latitude"], lon=item["longitude"])
            save_cfg(self.cfg)
            self.city_full = item["name"]
            self._fit_city()
            self._wx_state = ("…", FG)
            self._text(self.wx_txt, *self._wx_state)
            self.refresh_weather()
            p.destroy()

        def to_list(_=None):
            if results:
                lb.focus_set()
                lb.selection_clear(0, "end")
                lb.selection_set(0)
                lb.activate(0)

        e.bind("<Return>", search)
        e.bind("<Down>", to_list)
        for w in (e, lb, p):
            w.bind("<Escape>", lambda _: p.destroy())
        lb.bind("<Double-Button-1>", choose)
        lb.bind("<Return>", choose)
        p.after(50, e.focus_force)

        # Close the picker as soon as the focus goes elsewhere (click outside the window)
        def check_focus():
            if not p.winfo_exists():
                return
            try:
                w = p.focus_get()
            except (KeyError, tk.TclError):
                w = None
            if w is None or not str(w).startswith(str(p)):
                self.close_picker(force=True)

        p.bind("<FocusOut>", lambda _e: p.after(120, check_focus))

    def run(self):
        self.root.mainloop()


_lock = None


def release_lock():
    global _lock
    if _lock is not None:
        try:
            _lock.close()
        except OSError:
            pass
        _lock = None


if __name__ == "__main__":
    argv = sys.argv[1:]

    def _arg(name, default=None):
        return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default

    if "--setup" in argv:
        sys.exit(0 if run_setup(_arg("--user") or current_user(), start="--no-start" not in argv) else 1)
    if "--selftest" in argv:
        self_test(_arg("--selftest", "selftest.txt"))
        sys.exit(0)
    # Started without admin rights while the scheduled task exists: go through it
    # (admin rights without any prompt) to get the CPU temperature.
    if IS_WIN and FROZEN and not is_admin() and task_exists() and task_run():
        sys.exit(0)
    # Right after an update or the setup, the previous instance may take a moment
    # to release the lock
    for _ in range(40):
        _lock = single_instance()
        if _lock is not None or not FROZEN:
            break
        time.sleep(0.25)
    if _lock is None:
        sys.exit(0)  # already running
    Overlay().run()
