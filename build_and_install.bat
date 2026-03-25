@echo off
chcp 65001 >nul 2>&1
title RenderDoc MCP - Build and Install
echo ============================================================
echo   RenderDoc MCP - Build and Install
echo ============================================================
echo.

:: Run the PowerShell script with Bypass execution policy
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_and_install.ps1"

echo.
if %ERRORLEVEL% NEQ 0 (
    echo [FAILED] Build failed with error code %ERRORLEVEL%
) else (
    echo [DONE] Build and installation completed.
)

echo.
pause
