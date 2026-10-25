param(
    [ValidatePattern('^[A-Za-z0-9_-]{1,50}$')][string]$BatchId = 'M2_001',
    [ValidateSet('Direct', 'Kitchen')][string]$Mode = 'Kitchen',
    [string]$PdiHome = $env:PDI_HOME,
    [string]$PythonExe = (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
    [string]$ConfigPath = (Join-Path $PSScriptRoot 'config.json')
)
$ErrorActionPreference = 'Stop'
$code = 1
try {
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'Python tidak ditemukan. Siapkan .venv sesuai README.' }
    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) { throw 'Salin config.example.json menjadi config.json terlebih dahulu.' }
    if ($Mode -eq 'Kitchen' -and (-not $PdiHome -or -not (Test-Path -LiteralPath (Join-Path $PdiHome 'Kitchen.bat')))) {
        throw 'Isi PDI_HOME atau -PdiHome dengan folder data-integration.'
    }
    $PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
    $ConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path
    if ($Mode -eq 'Direct') {
        & $PythonExe (Join-Path $PSScriptRoot 'scripts\ingest.py') --config $ConfigPath --batch-id $BatchId
        $code = $LASTEXITCODE
    } else {
        $job = Join-Path $PSScriptRoot 'pentaho\ingestion.kjb'
        Push-Location -LiteralPath $PdiHome
        try {
            & '.\Kitchen.bat' "/file:$job" "/param:BATCH_ID=$BatchId" "/param:CONFIG_PATH=$ConfigPath" "/param:PYTHON_EXE=$PythonExe" '/level:Basic'
            $code = $LASTEXITCODE
        } finally { Pop-Location }
    }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    $code = 1
}
exit $code


