$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $VenvPython)) {
    python -m venv (Join-Path $ProjectRoot '.venv')
}

& $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $ProjectRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed with exit code $LASTEXITCODE" }
& $VenvPython -m unittest discover -s (Join-Path $ProjectRoot 'tests') -v
if ($LASTEXITCODE -ne 0) { throw "Tests failed with exit code $LASTEXITCODE" }
& $VenvPython -m PyInstaller --noconfirm --clean (Join-Path $ProjectRoot 'movie-review.spec')
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$ExeFiles = @(Get-ChildItem -LiteralPath (Join-Path $ProjectRoot 'dist') -Filter '*.exe' -File)
if ($ExeFiles.Count -ne 1) {
    throw "Expected exactly one EXE in dist, found $($ExeFiles.Count)"
}
$ExePath = $ExeFiles[0].FullName

$Hash = Get-FileHash -LiteralPath $ExePath -Algorithm SHA256
Write-Host "Build completed: $ExePath"
Write-Host "Size: $((Get-Item -LiteralPath $ExePath).Length) bytes"
Write-Host "SHA256: $($Hash.Hash)"
