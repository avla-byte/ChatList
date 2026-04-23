# Сборка одного .exe для Windows (GUI без консоли).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "Установка зависимостей для сборки…" -ForegroundColor Cyan
python -m pip install -q -r requirements-build.txt

$exeName = "ChatList"
Write-Host "Сборка $exeName.exe…" -ForegroundColor Cyan
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name $exeName `
    main.py

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Готово: dist\$exeName.exe" -ForegroundColor Green
