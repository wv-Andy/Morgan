<#
.SYNOPSIS
    Publica Morgan en internet mediante un túnel de Cloudflare.

.DESCRIPTION
    Morgan puede leer y escribir archivos, ejecutar comandos en PowerShell y
    terminar procesos de ESTE equipo. Sacarlo a internet sin autenticación
    equivale a dejar una consola abierta a cualquiera que dé con la URL.

    Por eso el script se NIEGA a arrancar si MORGAN_API_TOKEN no está definido.
    No es una advertencia que se pueda ignorar: es una comprobación previa.

.EXAMPLE
    .\scripts\morgan-remoto.ps1
    Construye la web, arranca la API en 127.0.0.1:8000 y abre el túnel.

.EXAMPLE
    .\scripts\morgan-remoto.ps1 -SinConstruir
    Omite el build del frontend (útil si no lo has tocado).
#>

[CmdletBinding()]
param(
    [switch]$SinConstruir,
    [int]$Puerto = 8000
)

$ErrorActionPreference = 'Stop'
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

function Escribe($texto, $color = 'Gray') { Write-Host "  $texto" -ForegroundColor $color }

Write-Host ""
Write-Host "  Morgan — acceso remoto" -ForegroundColor Cyan
Write-Host "  ──────────────────────" -ForegroundColor DarkGray
Write-Host ""

# --- 1. Comprobación de seguridad, antes que nada ---------------------------
$env:MORGAN_API_TOKEN = $null
$rutaEnv = Join-Path $raiz '.env'
if (-not (Test-Path $rutaEnv)) {
    Escribe "No existe .env. Copia .env.example y configúralo." Red
    exit 1
}

$token = $null
foreach ($linea in Get-Content $rutaEnv) {
    if ($linea -match '^\s*MORGAN_API_TOKEN\s*=\s*(.+)\s*$') { $token = $Matches[1].Trim() }
}

if ([string]::IsNullOrWhiteSpace($token)) {
    Escribe "MORGAN_API_TOKEN no está definido en .env." Red
    Write-Host ""
    Escribe "Morgan ejecuta comandos y borra archivos en ESTE equipo." Yellow
    Escribe "Publicarlo sin token deja esa capacidad abierta a cualquiera." Yellow
    Write-Host ""
    Escribe "Genera uno con:" Gray
    Escribe '  .\venv\Scripts\python -c "from src.api.auth import generate_token; print(generate_token())"' DarkGray
    exit 1
}

Escribe "Token de la API configurado ✔" Green

# --- 2. cloudflared ----------------------------------------------------------
$cloudflared = @(
    "C:\Program Files (x86)\cloudflared\cloudflared.exe",
    "C:\Program Files\cloudflared\cloudflared.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $cloudflared) {
    $enPath = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($enPath) { $cloudflared = $enPath.Source }
}

if (-not $cloudflared) {
    Escribe "cloudflared no está instalado." Red
    Escribe "Instálalo con:  winget install Cloudflare.cloudflared" Gray
    exit 1
}

Escribe "cloudflared encontrado ✔" Green

# --- 3. Frontend -------------------------------------------------------------
if (-not $SinConstruir) {
    Escribe "Construyendo la interfaz..." Gray
    npm --prefix web run build 2>&1 | Select-Object -Last 1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Escribe "Falló el build del frontend." Red; exit 1 }
    Escribe "Interfaz construida ✔" Green
}

# --- 4. API ------------------------------------------------------------------
# Escucha solo en 127.0.0.1: quien entra desde fuera lo hace por el túnel, que
# es el único camino, y ese camino exige el token.
$env:MORGAN_API_HOST = '127.0.0.1'
$env:MORGAN_API_PORT = "$Puerto"

$python = Join-Path $raiz 'venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

Escribe "Arrancando Morgan en 127.0.0.1:$Puerto..." Gray
$api = Start-Process -FilePath $python -ArgumentList '-m','src.api.server' -PassThru -WindowStyle Hidden

Start-Sleep -Seconds 8
try {
    $salud = Invoke-WebRequest -Uri "http://127.0.0.1:$Puerto/health" -UseBasicParsing -TimeoutSec 10
    if ($salud.StatusCode -ne 200) { throw "respuesta $($salud.StatusCode)" }
    Escribe "Morgan responde ✔" Green
} catch {
    Escribe "Morgan no arrancó: $_" Red
    if (-not $api.HasExited) { $api.Kill() }
    exit 1
}

# --- 5. Túnel ----------------------------------------------------------------
Write-Host ""
Escribe "Abriendo el túnel. La URL pública aparecerá abajo." Cyan
Escribe "Al abrirla, Morgan te pedirá el token: es el de tu .env." Gray
Escribe "Ctrl+C cierra el túnel y detiene Morgan." DarkGray
Write-Host ""

try {
    & $cloudflared tunnel --url "http://127.0.0.1:$Puerto"
} finally {
    Write-Host ""
    Escribe "Cerrando Morgan..." Gray
    if (-not $api.HasExited) { $api.Kill() }
    Escribe "Listo." Green
}
