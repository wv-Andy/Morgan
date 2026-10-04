<#
La ventana única de Morgan para Windows (5.3), por dentro, en GitHub Actions.

Instala el programa (aislado, como las demás pruebas) y le pone encima **la copia de prueba**
de Morgan.exe, compilada con la característica `depurar`: es la única que abre el protocolo de
depuración de WebView2 (WebView2 ignora su variable de entorno porque Tauri le pasa sus propios
argumentos, medido). Lo que se publica se compila sin ella. Los permisos de Tauri (qué puede
pedir la web) van dentro de las dos copias por igual: se mide la misma frontera.

Luego `probar_ventana.py` mira la página: la pantalla de emparejar, la web en la misma ventana,
que la web solo pueda pedir lo inofensivo y que lo ajeno no se cargue.

    powershell -ExecutionPolicy Bypass -File empaquetado\probar_ventana.ps1 -Instalador <.exe> -Depurar <Morgan.exe de prueba>
#>
param([Parameter(Mandatory = $true)][string]$Instalador,
      [Parameter(Mandatory = $true)][string]$Depurar)

$ErrorActionPreference = "Stop"
$instaladoDeVerdad = [bool](Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall -ErrorAction SilentlyContinue |
    Where-Object { (Get-ItemProperty $_.PSPath).DisplayName -eq "Morgan para Windows" })
if ($instaladoDeVerdad -or $env:GITHUB_ACTIONS -ne "true") {
    throw "Esta prueba solo corre en GitHub Actions, en una máquina sin Morgan para Windows."
}

$raiz = Join-Path $env:USERPROFILE "morgan-prueba-ventana"
$instalado = Join-Path $raiz "programa"
$CLAVE = "HKCU:\Software\Morgan\Morgan para Windows"
$PUERTO = 9333
Remove-Item -Recurse -Force $raiz -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $raiz | Out-Null
$env:MORGAN_AGENTE_DIR = Join-Path $raiz "estado"
$env:MORGAN_CARPETA_INICIO = Join-Path $raiz "inicio"
$env:MORGAN_CARPETA_MENU = Join-Path $raiz "menu"
$codigo = 1
try {
    $p = Start-Process $Instalador -ArgumentList @("/" + "S", "/D=$instalado") -PassThru -Wait
    if ($p.ExitCode -ne 0) { throw "no se instaló" }
    Get-Process Morgan -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$instalado*" } | Stop-Process -Force
    Copy-Item $Depurar (Join-Path $instalado "Morgan.exe") -Force
    $env:MORGAN_DEPURAR_PUERTO = "$PUERTO"
    Start-Process (Join-Path $instalado "Morgan.exe") | Out-Null
    python (Join-Path $PSScriptRoot "probar_ventana.py") --puerto $PUERTO --estado $env:MORGAN_AGENTE_DIR
    $codigo = $LASTEXITCODE
}
finally {
    Get-Process Morgan -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$instalado*" } | Stop-Process -Force
    if (Test-Path (Join-Path $instalado "uninstall.exe")) {
        Start-Process (Join-Path $instalado "uninstall.exe") -ArgumentList @("/" + "S") -Wait
    }
    if ("$((Get-ItemProperty $CLAVE -ErrorAction SilentlyContinue).'(default)')" -like "$raiz*") {
        Remove-Item $CLAVE -Recurse -Force
    }
    Remove-Item Env:MORGAN_AGENTE_DIR, Env:MORGAN_CARPETA_INICIO, Env:MORGAN_CARPETA_MENU, Env:MORGAN_DEPURAR_PUERTO -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $raiz -ErrorAction SilentlyContinue
}
exit $codigo
