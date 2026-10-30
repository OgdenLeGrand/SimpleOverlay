# Downloads the Inter font (SIL Open Font License) into ./fonts so it can be bundled in the exe.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Invoke-WebRequest "https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip" -OutFile inter.zip
Expand-Archive inter.zip -DestinationPath inter -Force
New-Item -ItemType Directory -Force fonts | Out-Null
foreach ($n in 'Inter-Regular.ttf', 'Inter-Bold.ttf') {
  $f = Get-ChildItem inter -Recurse -Filter $n | Select-Object -First 1
  if ($f) { Copy-Item $f.FullName fonts\ }
}
if (-not (Test-Path fonts\Inter-Regular.ttf)) {
  # fallback: variable font
  $f = Get-ChildItem inter -Recurse -Filter 'InterVariable.ttf' | Select-Object -First 1
  Copy-Item $f.FullName fonts\
}
$lic = Get-ChildItem inter -Recurse -Filter 'LICENSE.txt' | Select-Object -First 1
if ($lic) { Copy-Item $lic.FullName fonts\Inter-LICENSE.txt }
Get-ChildItem fonts | ForEach-Object { Write-Host "fonts/$($_.Name) $($_.Length)" }
