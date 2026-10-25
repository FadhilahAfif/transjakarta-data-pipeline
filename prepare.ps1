param(
    [string]$PythonExe = 'python',
    [string]$InitializeSourceCsv,
    [switch]$JobOnly
)
$ErrorActionPreference = 'Stop'
try {
    $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        & $PythonExe -m venv (Join-Path $PSScriptRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Pembuatan virtual environment gagal.' }
    }
    if (-not $JobOnly) {
        & $python -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Instalasi dependensi gagal.' }
        $config = Join-Path $PSScriptRoot 'config.json'
        if (-not (Test-Path -LiteralPath $config)) {
            Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'config.example.json') -Destination $config
            throw 'config.json dibuat. Isi koneksi dan password, lalu jalankan kembali.'
        }
    }
    $setupArgs = @()
    if ($JobOnly) { $setupArgs += '--job-only' }
    if ($InitializeSourceCsv) {
        if ($JobOnly) { throw 'JobOnly tidak boleh digabung dengan InitializeSourceCsv.' }
        $setupArgs += @('--initialize-source', (Resolve-Path -LiteralPath $InitializeSourceCsv).Path)
    }
    & $python (Join-Path $PSScriptRoot 'scripts\setup_project.py') @setupArgs
    if ($LASTEXITCODE -ne 0) { throw 'Persiapan gagal. Periksa pesan di atas.' }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
