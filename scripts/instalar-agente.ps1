# Instalar el agente local de Morgan en un PC con Windows (3.8).
#
# Lo sirve la nube en /agente/instalar.ps1, y la web da la línea para pegarlo en
# PowerShell con tu código de emparejamiento (se baja a un fichero y se ejecuta; la forma
# «bajar y ejecutar en memoria» la bloquea Windows):
#
#   irm https://morgan-ia-2-0.onrender.com/agente/instalar.ps1 -OutFile $env:TEMP\instalar-morgan.ps1;
#   powershell -NoProfile -ExecutionPolicy Bypass -File $env:TEMP\instalar-morgan.ps1 -Codigo K7QF-2M9D
#
# Qué hace, y nada más:
#   1. Busca Python 3.12 o más; si no hay, lo instala con winget para tu usuario (preguntando).
#   2. Pide a la nube la última versión del agente, con tu código (no lo gasta).
#   3. Baja el paquete y comprueba que su SHA-256 es el del manifiesto.
#   4. Lo abre en %LOCALAPPDATA%\Morgan\agente\app\<versión>, solo con lo permitido.
#   5. Le crea su propio entorno de Python e instala sus dependencias.
#   6. Pasa al agente: él comprueba MI FIRMA, empareja el PC (te enseña la cuenta
#      y te pide confirmar), lo pone a arrancar con Windows, comprueba que conecta y abre
#      «Morgan en tu PC» para elegir carpetas y capacidades (también en el menú Inicio).
#
# Sin permisos de administrador. Nada se instala fuera de tu carpeta de usuario.

param(
    [Parameter(Mandatory = $true)][string]$Codigo,
    [string]$Nube = "https://morgan-ia-2-0.onrender.com"
)

# Sin «Stop» global: en PowerShell 5.1, cualquier cosa que un programa escriba por stderr
# (el gestor de Python avisa así de que se actualizó) sería un error fatal. Medido al
# probarlo. Cada orden de PowerShell lleva su -ErrorAction Stop, y los programas se
# juzgan por su código de salida.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

function Parar([string]$texto) {
    Write-Host ""
    Write-Host "No se instaló: $texto" -ForegroundColor Red
    exit 1
}

# --- 1. Python ---------------------------------------------------------------------------
# Solo la última línea: el gestor de Python de Windows (py 26+) puede bajarse e instalar
# un Python la primera vez y escribir su progreso por la misma salida (medido al probarlo).
function Buscar-Python {
    $candidatos = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidatos += (& py -3 -c "import sys; print(sys.executable)" 2>$null | Select-Object -Last 1)
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $candidatos += (& python -c "import sys; print(sys.executable)" 2>$null | Select-Object -Last 1)
    }
    # Donde lo deja winget al instalarlo para el usuario, aunque el PATH aún no lo sepa.
    $candidatos += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | ForEach-Object { $_.FullName }
    foreach ($c in $candidatos) {
        if ($c -and (Test-Path $c)) {
            if ((& $c -c "import sys; print(sys.version_info >= (3, 12))" 2>$null) -eq "True") { return $c }
        }
    }
    return $null
}

$python = Buscar-Python
if (-not $python) {
    # 4.17 (decisión mía): quien no tiene Python no tiene por qué saber instalarlo.
    Write-Host "Morgan necesita Python 3.12 o más, y no está en este PC."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Parar "no está winget para instalarlo solo. Bájalo de https://www.python.org/downloads/ (marca «Add python.exe to PATH») y vuelve a pegar la línea."
    }
    $respuesta = Read-Host "¿Lo instalo ahora, solo para tu usuario y sin administrador? [S/n]"
    if ($respuesta -match '^(n|no)$') {
        Parar "sin Python no se puede. Cuando quieras: winget install Python.Python.3.13 --scope user"
    }
    Write-Host "Instalando Python (un par de minutos)..."
    & winget install --id Python.Python.3.13 --exact --scope user --silent --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
    $python = Buscar-Python
    if (-not $python) {
        Parar "Python no quedó instalado. Instálalo de https://www.python.org/downloads/ y vuelve a pegar la línea."
    }
}
Write-Host "Python: $python"

# --- 2 y 3. La versión publicada y su paquete -----------------------------------------------
$cabeceras = @{ "X-Morgan-Codigo" = $Codigo }
try {
    $publicada = Invoke-RestMethod "$Nube/agente/actualizacion" -Headers $cabeceras -ErrorAction Stop
} catch {
    Parar "la nube no aceptó el código (¿caducó? duran 10 minutos). Pide otro en la web: Ajustes, Tu equipo."
}
if (-not $publicada.manifiesto) { Parar "todavía no hay ninguna versión del agente publicada." }
$manifiesto = $publicada.manifiesto | ConvertFrom-Json
$version = [string]$manifiesto.version
if ($version -notmatch '^\d+\.\d+(\.\d+)?(-dev)?$') { Parar "la versión publicada no se entiende." }

$app = Join-Path $env:LOCALAPPDATA "Morgan\agente\app"
$destino = Join-Path $app $version
$nueva = "$destino.nueva"
$zip = Join-Path $env:TEMP "morgan-agente-$version.zip"
Write-Host "Bajando el agente $version..."
try {
    Invoke-WebRequest "$Nube/agente/paquete" -Headers $cabeceras -OutFile $zip -UseBasicParsing -ErrorAction Stop
} catch {
    Parar "no se pudo bajar el paquete."
}
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
if ($hash -ne [string]$manifiesto.sha256) { Parar "el paquete que llegó no es el publicado (hash distinto)." }

# --- 4. Abrirlo, solo con lo permitido (las mismas reglas que src/agente/firma.py) ----------
$abrir = Join-Path $env:TEMP "morgan-abrir-paquete.py"
@'
import re, sys, zipfile
from pathlib import Path, PurePosixPath

PERMITIDO = (re.compile(r"^src/__init__\.py$"), re.compile(r"^src/agente/[A-Za-z0-9_/]+\.py$"),
             re.compile(r"^requirements-agente\.txt$"))
destino = Path(sys.argv[2])
with zipfile.ZipFile(sys.argv[1]) as zf:
    for info in zf.infolist():
        if info.is_dir():
            continue
        nombre = info.filename
        partes = PurePosixPath(nombre).parts
        if nombre.startswith("/") or ":" in nombre or ".." in partes or not any(p.match(nombre) for p in PERMITIDO):
            sys.exit("El paquete trae algo fuera de su sitio: " + nombre)
        ruta = destino.joinpath(*partes)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(zf.read(info))
'@ | Set-Content -Path $abrir -Encoding UTF8 -ErrorAction Stop

if (Test-Path $nueva) { Remove-Item $nueva -Recurse -Force }
& $python $abrir $zip $nueva
if ($LASTEXITCODE -ne 0) { Parar "el paquete no se pudo abrir." }

# --- 5. Su entorno y sus dependencias ------------------------------------------------------
Write-Host "Preparando su entorno de Python (un minuto)..."
& $python -m venv (Join-Path $nueva "venv")
if ($LASTEXITCODE -ne 0) { Parar "no se pudo crear el entorno de Python." }
$py_agente = Join-Path $nueva "venv\Scripts\python.exe"
& $py_agente -m pip install --disable-pip-version-check --no-input -q -r (Join-Path $nueva "requirements-agente.txt")
if ($LASTEXITCODE -ne 0) { Parar "no se pudieron instalar sus dependencias (¿hay internet?)." }
if (Test-Path $destino) { Remove-Item $destino -Recurse -Force -ErrorAction Stop }
Move-Item $nueva $destino -ErrorAction Stop

# --- 6. El agente sigue: firma, emparejar, arranque y comprobar ---------------------------------
Set-Location $destino
& (Join-Path $destino "venv\Scripts\python.exe") -m src.agente instalar --nube $Nube --codigo $Codigo --paquete $zip
$final = $LASTEXITCODE
Remove-Item $zip, $abrir -ErrorAction SilentlyContinue
exit $final
