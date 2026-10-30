@echo off
title SimpleOverlay setup
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm 'https://raw.githubusercontent.com/OgdenLeGrand/SimpleOverlay/main/install.ps1' | iex"
if errorlevel 1 echo. & echo   Something went wrong during the installation.
pause
