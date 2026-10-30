"""Gathers LibreHardwareMonitorLib (.NET Framework 4.7.2) and its dependencies into ./lhm
from the NuGet packages downloaded to ./pkgs (used by the CI)."""
import glob
import os
import shutil

base, out = os.path.abspath("pkgs"), os.path.abspath("lhm")
os.makedirs(out, exist_ok=True)
lib = glob.glob(os.path.join(base, "LibreHardwareMonitorLib.*", "runtimes", "win-x64", "lib", "net472",
                             "LibreHardwareMonitorLib.dll"))
assert lib, "LibreHardwareMonitorLib.dll (net472, win-x64) not found"
shutil.copy(lib[0], out)
prefs = ["net472", "net471", "net47", "net462", "net461", "net46", "netstandard2.0"]
for pkg in glob.glob(os.path.join(base, "*")):
    if os.path.basename(pkg).startswith("LibreHardwareMonitorLib."):
        continue
    dirs = {os.path.basename(d): d for d in glob.glob(os.path.join(pkg, "lib", "*"))}
    for p in prefs:
        if p in dirs:
            for f in glob.glob(os.path.join(dirs[p], "*.dll")):
                shutil.copy(f, out)
            break
print("lhm/:", sorted(os.listdir(out)))
