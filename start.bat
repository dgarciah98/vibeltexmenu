@echo off
if exist "VibeltexLauncher.exe" (
    start "" "VibeltexLauncher.exe"
) else (
    python .\main.py
)