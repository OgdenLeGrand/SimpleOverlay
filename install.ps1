# Installs (or reinstalls) SimpleOverlay for the current user.
# A single administrator prompt is shown, for the CPU temperature
# (PawnIO driver + scheduled tasks that then start the overlay without any prompt).
$ErrorActionPreference = 'Stop'
$repo    = 'OgdenLeGrand/SimpleOverlay'
$dir     = Join-Path $env:LOCALAPPDATA 'SimpleOverlay'
$exe     = Join-Path $dir 'SimpleOverlay.exe'
$runKey  = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$desktop = [Environment]::GetFolderPath('Desktop')

Write-Host ''
Write-Host '  === Installing SimpleOverlay ===' -ForegroundColor Cyan
Write-Host ''

# Stop running instances
Get-Process SimpleOverlay -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

Write-Host '[1/3] Downloading the latest version...'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
if (Test-Path $exe) {
  # a running exe cannot be overwritten, but it can be renamed
  Remove-Item "$exe.old" -Force -ErrorAction SilentlyContinue
  Rename-Item $exe "$exe.old" -ErrorAction SilentlyContinue
}
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'
Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/$repo/releases/latest/download/SimpleOverlay.exe" -OutFile $exe

$s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'SimpleOverlay.lnk'))
$s.TargetPath = $exe
$s.Save()

Write-Host '[2/3] CPU temperature: Windows will ask for administrator approval (only once)...'
$ok = $false
try {
  $user = "$env:USERDOMAIN\$env:USERNAME"
  $p = Start-Process $exe -ArgumentList '--setup', '--user', "`"$user`"" -Verb RunAs -Wait -PassThru
  $ok = ($p.ExitCode -eq 0)
} catch {
  $ok = $false
}

Write-Host '[3/3] Starting...'
if ($ok) {
  # the setup created the scheduled tasks and already started the overlay
  Write-Host ''
  Write-Host '  Done! SimpleOverlay is running with the CPU temperature,' -ForegroundColor Green
  Write-Host '  and will start with Windows without any further prompt.' -ForegroundColor Green
} else {
  Set-ItemProperty -Path $runKey -Name SimpleOverlay -Value "`"$exe`""
  Start-Process $exe
  Write-Host ''
  Write-Host '  SimpleOverlay is running, but without the CPU temperature (approval declined).' -ForegroundColor Yellow
  Write-Host '  You can enable it later: right-click the overlay > Enable CPU temperature.'
}
Write-Host '  Updates are installed from the overlay itself (right-click).'
Write-Host ''
