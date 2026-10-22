[CmdletBinding()]
param([ValidateRange(1024,65535)][int]$Port = 8003, [string]$Python)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& "$PSScriptRoot\start.ps1" -InstallAI -PrepareOnly -Python $Python
if (-not (Test-Path -LiteralPath "$PSScriptRoot\.venv\Scripts\python.exe")) { throw 'Python setup did not finish.' }
& "$PSScriptRoot\.venv\Scripts\python.exe" "$PSScriptRoot\tools\serve_demo.py" --port $Port --download-tunnel
exit $LASTEXITCODE
