<#
Emparejar el programa de Windows de verdad (5.0), contra morgan-carga y nunca producción.

Crea una cuenta de prueba en morgan-carga, pide un código como lo hace la web, instala el
programa aislado (estado, Inicio y menú en una carpeta de prueba), lo empareja con **la misma
orden que lanza su ventana** (`emparejar --codigo`, y `arranque activar`), mira en la nube que
el PC aparece conectado, y desinstala: el PC tiene que quedar revocado en la nube y lo real
de este PC, intacto (huella antes y después).

    powershell -ExecutionPolicy Bypass -File empaquetado\probar_emparejar.ps1 -Instalador <.exe>
#>
param([Parameter(Mandatory = $true)][string]$Instalador,
      [string]$Nube = "https://morgan-carga.onrender.com")

$ErrorActionPreference = "Stop"
if ($Nube -match "morgan-ia-2-0") { throw "Contra producción no: esta prueba crea una cuenta." }
# Con el programa instalado de verdad, uno de prueba comparte su entrada de «Aplicaciones
# instaladas», sus accesos y su clave Run (5.1): desinstalar el de prueba se llevaría los de verdad.
$instaladoDeVerdad = [bool](Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall -ErrorAction SilentlyContinue |
    Where-Object { (Get-ItemProperty $_.PSPath).DisplayName -eq "Morgan para Windows" })
if ($instaladoDeVerdad -and $env:GITHUB_ACTIONS -ne "true") {
    throw "Morgan para Windows está instalado en este PC: la prueba se llevaría el de verdad."
}

$raiz = Join-Path $env:USERPROFILE "morgan-prueba-emparejar"
$instalado = Join-Path $raiz "programa"
Remove-Item -Recurse -Force $raiz -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $raiz | Out-Null

function Huella {
    $rutas = @("$env:LOCALAPPDATA\Morgan\agente\credencial.bin", "$env:LOCALAPPDATA\Morgan\agente\politica.json",
               "$env:LOCALAPPDATA\Morgan\agente\agente.json")
    $rutas += Get-ChildItem ([Environment]::GetFolderPath('Startup')), ([Environment]::GetFolderPath('Programs')) `
        -Filter "Morgan*.lnk" -ErrorAction SilentlyContinue |
        Where-Object Name -notlike "Morgan para Windows*" | ForEach-Object FullName   # el del programa, no
    $rutas | Where-Object { Test-Path $_ } | ForEach-Object { "$_|" + (Get-FileHash $_ -Algorithm SHA256).Hash }
}
$antes = Huella
$r = [ordered]@{}

# 1. Una cuenta de prueba en morgan-carga y un código, como la web.
$sesion = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$nombre = "prueba-programa-" + (Get-Random -Maximum 99999)
$cuerpo = @{ username = $nombre; email = "$nombre@ejemplo.co"; password = "contrasena-larga-de-prueba" } | ConvertTo-Json
$alta = Invoke-RestMethod "$Nube/auth/registro" -Method Post -Body $cuerpo -ContentType "application/json" `
    -WebSession $sesion -TimeoutSec 120
$cabeceras = @{ "X-Morgan-CSRF" = $alta.csrf }
$codigo = (Invoke-RestMethod "$Nube/auth/agentes/codigo" -Method Post -Headers $cabeceras -WebSession $sesion).codigo
$r.cuenta = "$nombre (código pedido)"

$env:MORGAN_AGENTE_DIR = Join-Path $raiz "estado"
$env:MORGAN_CARPETA_INICIO = Join-Path $raiz "inicio"
$env:MORGAN_CARPETA_MENU = Join-Path $raiz "menu"
$env:MORGAN_NUBE = $Nube
try {
    Start-Process $Instalador -ArgumentList @("/" + "S", "/D=$instalado") -Wait
    $agente = Join-Path $instalado "agente\morgan-agente\morgan-agente.exe"

    # 2. Lo mismo que hace el botón «Conectar» de la ventana (escritorio/src-tauri/src/main.rs).
    # Sin parar en la salida de error del agente: se quiere ver lo que dice.
    $ErrorActionPreference = "Continue"
    #    Primero, de qué cuenta es (sin usarlo), como enseña la ventana; después, con --si.
    $cuenta = & $agente emparejar --codigo $codigo --consultar 2>&1
    $r.cuenta_que_enseña = ("$cuenta" -match "CUENTA: $nombre")
    $salida = & $agente emparejar --codigo $codigo --si 2>&1
    $r.emparejado = ($LASTEXITCODE -eq 0)
    $salida2 = & $agente arranque activar 2>&1
    $r.arranque_activado = ($LASTEXITCODE -eq 0) -and (Test-Path (Join-Path $env:MORGAN_CARPETA_INICIO "*.lnk"))

    # 3. En la nube: el PC aparece, y conectado.
    $conectado = $false
    $limite = (Get-Date).AddSeconds(60)
    while (-not $conectado -and (Get-Date) -lt $limite) {
        Start-Sleep 3
        $equipos = Invoke-RestMethod "$Nube/auth/agentes" -WebSession $sesion
        $lista = if ($equipos.agentes) { $equipos.agentes } else { $equipos }
        $conectado = [bool]($lista | Where-Object { $_.conectado })
    }
    $r.conectado_en_la_nube = $conectado

    # 4. Desinstalar: para el agente, desempareja (la nube lo revoca) y se va.
    Start-Process (Join-Path $instalado "uninstall.exe") -ArgumentList @("/" + "S") -Wait
    Start-Sleep 5
    $equipos = Invoke-RestMethod "$Nube/auth/agentes" -WebSession $sesion
    $lista = if ($equipos.agentes) { $equipos.agentes } else { $equipos }
    $r.revocado_al_desinstalar = -not [bool]($lista | Where-Object { $_.estado -eq "activo" })
    $r.sin_procesos = -not [bool](Get-Process | Where-Object { $_.Path -like "$instalado*" })
}
finally {
    # La ruta de instalación que recuerda el registro: si es la de la prueba, fuera. Si no, el
    # próximo instalador la propone: el 2026-10-04 mi programa de verdad acabó instalado dentro
    # de la carpeta de esta prueba (que la prueba borra al empezar).
    $claveDelPrograma = "HKCU:\Software\Morgan\Morgan para Windows"
    if ("$((Get-ItemProperty $claveDelPrograma -ErrorAction SilentlyContinue).'(default)')" -like "$raiz*") {
        Remove-Item $claveDelPrograma -Recurse -Force
    }
    # Si algo falló a mitad, se desinstala igual: si no, quedaban el acceso del programa y su
    # entrada en «Aplicaciones instaladas» (pasó en la primera ejecución).
    $desinstalador = Join-Path $instalado "uninstall.exe"
    if (Test-Path $desinstalador) { Start-Process $desinstalador -ArgumentList @("/" + "S") -Wait; Start-Sleep 3 }
    Remove-Item Env:MORGAN_AGENTE_DIR, Env:MORGAN_CARPETA_INICIO, Env:MORGAN_CARPETA_MENU, Env:MORGAN_NUBE -ErrorAction SilentlyContinue
    Get-Process | Where-Object { $_.Path -like "$raiz*" } | Stop-Process -Force -ErrorAction SilentlyContinue
    $r.lo_real_intacto = -not (Compare-Object @($antes) @(Huella))
    Remove-Item -Recurse -Force $raiz -ErrorAction SilentlyContinue
}
$r.GetEnumerator() | ForEach-Object { "{0,-26} {1}" -f $_.Key, $_.Value }
if (-not $r.conectado_en_la_nube) { "--- lo que dijo el agente:"; $salida | Out-String; $salida2 | Out-String }
if (-not $r.lo_real_intacto) { Write-Error "CAMBIÓ ALGO REAL DEL AGENTE: revisar ya." }
