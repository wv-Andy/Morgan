<#
La prueba del instalador de Morgan para Windows (5.0), en un PC que ya tiene Morgan.

Instala en una carpeta de prueba, abre el programa y lo captura, y desinstala. **Todo lo del
agente va aislado**: su estado (MORGAN_AGENTE_DIR), la carpeta de Inicio
(MORGAN_CARPETA_INICIO) y la del menú (MORGAN_CARPETA_MENU). La primera vez solo se aisló el
estado, y el `desinstalar` del gancho borró el acceso de Inicio y el de «Morgan en tu PC» de
verdad (2026-10-03). Por eso se toma huella de lo real antes y después: si cambia, falla.

    powershell -ExecutionPolicy Bypass -File empaquetado\probar_instalador.ps1 -Instalador <ruta del .exe>
#>
param([Parameter(Mandatory = $true)][string]$Instalador)

$ErrorActionPreference = "Stop"
$raiz = Join-Path $env:USERPROFILE "morgan-prueba-instalador"
$instalado = Join-Path $raiz "programa"
Remove-Item -Recurse -Force $raiz -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $raiz | Out-Null

# Lo real que no se puede tocar: el estado del agente de verdad, su Inicio y su menú.
function Huella {
    $rutas = @("$env:LOCALAPPDATA\Morgan\agente\credencial.bin", "$env:LOCALAPPDATA\Morgan\agente\politica.json",
               "$env:LOCALAPPDATA\Morgan\agente\agente.json")
    $rutas += Get-ChildItem ([Environment]::GetFolderPath('Startup')), ([Environment]::GetFolderPath('Programs')) `
        -Filter "Morgan*.lnk" -ErrorAction SilentlyContinue | ForEach-Object FullName
    $rutas | Where-Object { Test-Path $_ } | ForEach-Object {
        "$_|" + (Get-FileHash $_ -Algorithm SHA256).Hash
    }
}
$antes = Huella

$env:MORGAN_AGENTE_DIR = Join-Path $raiz "estado"
$env:MORGAN_CARPETA_INICIO = Join-Path $raiz "inicio"
$env:MORGAN_CARPETA_MENU = Join-Path $raiz "menu"
$resultado = [ordered]@{}
try {
    $p = Start-Process $Instalador -ArgumentList @("/" + "S", "/D=$instalado") -PassThru -Wait
    $resultado.instalado = ($p.ExitCode -eq 0) -and (Test-Path "$instalado\Morgan.exe") `
        -and (Test-Path "$instalado\agente\morgan-agente\morgan-agente-fondo.exe")
    $clave = Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall |
        Where-Object { (Get-ItemProperty $_.PSPath).DisplayName -eq "Morgan para Windows" }
    $resultado.en_aplicaciones_instaladas = [bool]$clave

    $estado = & "$instalado\agente\morgan-agente\morgan-agente.exe" estado 2>&1 | Select-Object -First 1
    $resultado.agente = "$estado"

    $app = Start-Process "$instalado\Morgan.exe" -PassThru
    Start-Sleep 6
    $resultado.ventana = (Get-Process -Id $app.Id).MainWindowTitle
    Stop-Process -Id $app.Id -Force

    New-Item -ItemType Directory -Force (Join-Path $env:MORGAN_AGENTE_DIR "respaldos") | Out-Null
    "respaldo" | Set-Content (Join-Path $env:MORGAN_AGENTE_DIR "respaldos\r.txt")
    $p = Start-Process (Join-Path $instalado "uninstall.exe") -ArgumentList @("/" + "S") -PassThru -Wait
    Start-Sleep 5
    $resultado.desinstalado = ($p.ExitCode -eq 0) -and -not (Test-Path "$instalado\Morgan.exe")
    $resultado.fuera_de_aplicaciones = -not [bool](Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall |
        Where-Object { (Get-ItemProperty $_.PSPath).DisplayName -eq "Morgan para Windows" })
    $resultado.quedan_los_respaldos = Test-Path (Join-Path $env:MORGAN_AGENTE_DIR "respaldos\r.txt")
}
finally {
    Remove-Item Env:MORGAN_AGENTE_DIR, Env:MORGAN_CARPETA_INICIO, Env:MORGAN_CARPETA_MENU -ErrorAction SilentlyContinue
    $despues = Huella
    $resultado.lo_real_intacto = -not (Compare-Object @($antes) @($despues))
    Remove-Item -Recurse -Force $raiz -ErrorAction SilentlyContinue
}
$resultado.GetEnumerator() | ForEach-Object { "{0,-28} {1}" -f $_.Key, $_.Value }
if (-not $resultado.lo_real_intacto) { Write-Error "CAMBIÓ ALGO REAL DEL AGENTE: revisar ya." }
