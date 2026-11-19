@echo off
rem mediabox - comando per Windows (es. "mediabox status").
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0mediabox.ps1" %*
