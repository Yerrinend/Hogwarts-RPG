param([ValidateSet('pull','start','save','publish','status','validate')][string]$Action='pull',[int]$Chapter=0)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
$runtimePath=Join-Path $projectRoot '.local\runtime.json'
if (Test-Path -LiteralPath $runtimePath) {
    $runtime=Get-Content -LiteralPath $runtimePath -Raw | ConvertFrom-Json
    $pythonExe=$runtime.python
    $env:RPG_GIT=$runtime.git
} else {
    $pythonExe='python'
}
$syncArgs=@((Join-Path $PSScriptRoot 'rpg_sync.py'),$Action)
if ($Chapter -gt 0) {$syncArgs+=@('--chapter',[string]$Chapter)}
& $pythonExe @syncArgs
$result=$LASTEXITCODE
if ($Action -eq 'pull') {
    $logFolder=Join-Path $projectRoot '.local'
    if (!(Test-Path -LiteralPath $logFolder)) {New-Item -ItemType Directory -Path $logFolder | Out-Null}
    Set-Content -LiteralPath (Join-Path $logFolder 'startup-sync-status.txt') -Value ((Get-Date -Format o)+' exit='+$result)
}
exit $result
