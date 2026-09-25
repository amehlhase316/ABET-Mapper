Set-Location $PSScriptRoot

try {
    python -c "import requests" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "requests missing" }
} catch {
    Write-Host "The Python requests package is required."
    Write-Host "Run: python -m pip install -r requirements.txt"
    exit 1
}

$secureToken = Read-Host "Paste your Canvas API token" -AsSecureString
$tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
try {
    $env:ABET_MAPPER_CANVAS_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
    python server.py
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
    Remove-Item Env:ABET_MAPPER_CANVAS_TOKEN -ErrorAction SilentlyContinue
}
