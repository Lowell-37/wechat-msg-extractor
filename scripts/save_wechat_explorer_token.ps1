[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$activePython = if ($env:VIRTUAL_ENV) {
    Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
} else {
    $null
}
if ($activePython -and (Test-Path -LiteralPath $activePython)) {
    $python = $activePython
} elseif (Test-Path -LiteralPath $projectPython) {
    $python = $projectPython
} else {
    $python = "python"
}

$secureToken = Read-Host -Prompt "Paste the new WechatExplorer API Token" -AsSecureString
$bstr = [IntPtr]::Zero
try {
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    $env:WECHATEXPLORER_TOKEN_INPUT = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    Push-Location $repoRoot
    try {
        & $python -m services.credential_cli save
    }
    finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Remove-Item Env:WECHATEXPLORER_TOKEN_INPUT -ErrorAction SilentlyContinue
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}
