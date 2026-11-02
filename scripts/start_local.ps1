param([switch]$Smoke)
$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$ProjectRoot=(Get-Location).Path
$RuntimeRoot=Join-Path $ProjectRoot '.local\python312'
$PythonExe=Join-Path $RuntimeRoot 'tools\python.exe'
if (-not (Test-Path -LiteralPath $PythonExe)) {
    $DownloadRoot=Join-Path $ProjectRoot '.local\runtime-download'
    New-Item -ItemType Directory -Force -Path $DownloadRoot | Out-Null
    $ArchivePath=Join-Path $DownloadRoot 'python312.zip'
    Write-Host 'Downloading standalone CPython 3.12 from the official NuGet package...'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://www.nuget.org/api/v2/package/python/3.12.10' -OutFile $ArchivePath
    $HashAlgorithm=[System.Security.Cryptography.SHA256]::Create()
    $ArchiveStream=[System.IO.File]::OpenRead($ArchivePath)
    try {
        $ActualHash=[System.BitConverter]::ToString($HashAlgorithm.ComputeHash($ArchiveStream)).Replace('-','')
    } finally {
        $ArchiveStream.Dispose()
        $HashAlgorithm.Dispose()
    }
    if ($ActualHash -ne '0EB85C2DFCCCCF1B17352DE4C397F69194035B7D37149EACC16F1147D93DE3B8') {
        throw 'Python package checksum mismatch'
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Package=[System.IO.Compression.ZipFile]::OpenRead($ArchivePath)
    try {
        $RuntimePrefix=[System.IO.Path]::GetFullPath($RuntimeRoot)+[System.IO.Path]::DirectorySeparatorChar
        foreach ($Entry in $Package.Entries) {
            $Destination=[System.IO.Path]::GetFullPath((Join-Path $RuntimeRoot $Entry.FullName))
            if (-not $Destination.StartsWith($RuntimePrefix,[System.StringComparison]::OrdinalIgnoreCase)) {
                throw 'Invalid path in Python package'
            }
            if ($Entry.Name -eq '') {
                [System.IO.Directory]::CreateDirectory($Destination) | Out-Null
            } else {
                [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($Destination)) | Out-Null
                [System.IO.Compression.ZipFileExtensions]::ExtractToFile($Entry,$Destination,$true)
            }
        }
    } finally {
        $Package.Dispose()
    }
}
$VenvPython=Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython)) {
    & $PythonExe -m venv (Join-Path $ProjectRoot '.venv')
    if ($LASTEXITCODE -ne 0) {throw 'Unable to create the project virtual environment'}
}
& $VenvPython -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) {throw 'Dependency installation failed'}
if ($Smoke) {& $VenvPython -m scripts.run_local --smoke}
else {& $VenvPython -m scripts.run_local}
exit $LASTEXITCODE
