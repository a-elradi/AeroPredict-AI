@echo off
title AeroPredict Backend
cd /d "%~dp0backend"
echo [AeroPredict] Starting AI backend server...
python run.py
pause
