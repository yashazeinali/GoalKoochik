$ErrorActionPreference = 'Stop'
$runtime = Get-Command python -ErrorAction SilentlyContinue
if (-not $runtime) { throw 'Python 3.12 or newer is required. Install Python, then run this script again.' }
Set-Location -LiteralPath $PSScriptRoot
& $runtime.Source server.py --host 0.0.0.0 --port 8765
