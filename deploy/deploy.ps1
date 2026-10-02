param(
    [string]$Commit = '',
    [string]$Server = '179.255.107.220',
    [string]$ServerUser = 'deploy',
    [Parameter(Mandatory = $true)][string]$IdentityFile,
    [ValidateSet('all', 'prepare', 'trial', 'activate', 'verify', 'rollback')][string]$Phase = 'all'
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (-not $Commit) {
    $Commit = & git rev-parse HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Cannot determine the local Git commit.' }
}
if ($Commit -cnotmatch '^[0-9a-f]{40}$') { throw 'Use the full commit SHA.' }
if ($Server -notmatch '^[a-zA-Z0-9.-]+$' -or $ServerUser -notmatch '^[a-zA-Z0-9_-]+$') { throw 'Invalid server/user.' }
if (-not (Test-Path -LiteralPath $IdentityFile -PathType Leaf)) { throw 'SSH identity file is missing.' }
$remoteCommand = "sudo /usr/local/sbin/movie-review-deploy $Commit $Phase"
$ssh = Join-Path $env:WINDIR 'System32\OpenSSH\ssh.exe'
& $ssh -t -i $IdentityFile -o StrictHostKeyChecking=yes -o ConnectTimeout=15 "$ServerUser@$Server" $remoteCommand
if ($LASTEXITCODE -ne 0) { throw "Server deployment failed. Inspect retained evidence for $Commit." }
