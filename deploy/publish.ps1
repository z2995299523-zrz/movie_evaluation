param(
    [Parameter(Mandatory = $true)][string]$Message,
    [string]$DesktopPython = '.venv\Scripts\python.exe',
    [string]$WebPython = '.runtime-web\release-venv\Scripts\python.exe'
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    $result = & git -c http.sslBackend=openssl @GitArgs
    if ($LASTEXITCODE -ne 0) { throw "Git failed: $($GitArgs -join ' ')" }
    return $result
}
if ((Invoke-Git remote get-url origin) -ne 'https://github.com/z2995299523-zrz/movie_evaluation.git') {
    throw 'Unexpected origin; inspect the destination before publishing.'
}
if ((Invoke-Git branch --show-current) -ne 'main') { throw 'Publish from main.' }
if (Invoke-Git diff --cached --name-only) { throw 'Existing staged changes must be reviewed first.' }
Invoke-Git fetch origin main
Invoke-Git merge-base --is-ancestor origin/main HEAD
foreach ($check in @(@($DesktopPython, 'desktop'), @($WebPython, 'web'))) {
    & $check[0] deploy/check_release.py $check[1]
    if ($LASTEXITCODE -ne 0) { throw "Required $($check[1]) checks failed." }
}
$sourcePaths = @('.gitattributes', '.gitignore', '.github', 'README.md', 'app.py', 'index.html', 'graph_export.html',
    'movie-review.spec', 'build.ps1', 'requirements.txt', 'requirements-web.txt', 'requirements-web.lock',
    'deploy', 'tests', 'web', 'docs')
Invoke-Git add -- @sourcePaths
& $WebPython deploy/audit_source.py
if ($LASTEXITCODE -ne 0) { throw 'Source publication audit failed.' }
Invoke-Git diff --cached --check
if (Invoke-Git diff --cached --name-only) { Invoke-Git commit -m $Message }
Invoke-Git push origin main
$commit = Invoke-Git rev-parse HEAD
$remote = (Invoke-Git ls-remote origin refs/heads/main) -split '\s+'
if ($remote[0] -ne $commit) { throw 'The published remote head differs from the local commit.' }
Write-Host "Published Git commit: $commit"
Write-Host "Server command: sudo movie-review-deploy $commit"
