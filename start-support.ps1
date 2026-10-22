$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $env:AGI_KEY) {
    throw '请先设置 AGI_KEY 环境变量。'
}
Write-Host '说明书售后助手：http://127.0.0.1:8766'
python -m uvicorn support_demo.app:app --host 127.0.0.1 --port 8766
