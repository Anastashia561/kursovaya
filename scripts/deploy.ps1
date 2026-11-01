$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m scripts.deploy @args
if ($LASTEXITCODE -ne 0) {throw 'Deployment failed'}
