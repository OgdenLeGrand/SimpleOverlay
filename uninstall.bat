@echo off
title Uninstall SimpleOverlay
rem Administrator rights are required to remove the scheduled tasks
net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
taskkill /f /im SimpleOverlay.exe >nul 2>nul
timeout /t 1 /nobreak >nul
schtasks /delete /tn "SimpleOverlay" /f >nul 2>nul
schtasks /delete /tn "SimpleOverlay Startup" /f >nul 2>nul
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v SimpleOverlay /f >nul 2>nul
rmdir /s /q "%LOCALAPPDATA%\SimpleOverlay" 2>nul
powershell -NoProfile -Command "Remove-Item -ErrorAction SilentlyContinue ([Environment]::GetFolderPath('Desktop')+'\SimpleOverlay.lnk')" >nul 2>nul
del /q "%USERPROFILE%\.mini_overlay.json" "%USERPROFILE%\.simpleoverlay.log" "%USERPROFILE%\.simpleoverlay.setup" 2>nul
echo.
echo   SimpleOverlay has been uninstalled.
echo.
echo   The PawnIO driver (used for the CPU temperature) is kept, since other
echo   programs may use it. To remove it as well:
echo   Windows Settings ^> Apps ^> PawnIO ^> Uninstall.
echo.
pause
