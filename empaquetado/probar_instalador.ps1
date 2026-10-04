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
$RUN = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
function Instalado {
    # En una máquina limpia ni siquiera existe la clave Uninstall.
    [bool](Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall -ErrorAction SilentlyContinue |
        Where-Object { (Get-ItemProperty $_.PSPath).DisplayName -eq "Morgan para Windows" })
}
# Con el programa instalado de verdad, uno de prueba comparte su entrada de «Aplicaciones
# instaladas», sus accesos y su clave Run: desinstalar el de prueba se llevaría los de verdad.
# Por eso esta prueba corre en GitHub Actions (una máquina limpia), no en un PC con Morgan.
if ((Instalado) -and $env:GITHUB_ACTIONS -ne "true") {
    throw "Morgan para Windows está instalado en este PC: la prueba se llevaría el de verdad. Córrela en GitHub Actions."
}
$raiz = Join-Path $env:USERPROFILE "morgan-prueba-instalador"
# La ventana de Morgan de un proceso, por accesibilidad: el título de la «ventana principal»
# no vale (5.1): el proceso tiene también la ventana oculta de la instancia única, y
# `CloseMainWindow` la cerraba a ella y rompía la instancia única.
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
function VentanaDeMorgan([int]$id) {
    $A = [System.Windows.Automation.AutomationElement]
    $condicion = New-Object System.Windows.Automation.AndCondition(
        (New-Object System.Windows.Automation.PropertyCondition($A::ProcessIdProperty, $id)),
        (New-Object System.Windows.Automation.PropertyCondition($A::NameProperty, "Morgan")))
    $A::RootElement.FindFirst([System.Windows.Automation.TreeScope]::Children, $condicion)
}
# La de la conversación (5.2) es la grande: la del programa mide 480 de ancho.
function VentanaDeLaConversacion([int]$id) {
    $A = [System.Windows.Automation.AutomationElement]
    $condicion = New-Object System.Windows.Automation.AndCondition(
        (New-Object System.Windows.Automation.PropertyCondition($A::ProcessIdProperty, $id)),
        (New-Object System.Windows.Automation.PropertyCondition($A::NameProperty, "Morgan")))
    $A::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, $condicion) |
        Where-Object { $_.Current.BoundingRectangle.Width -gt 800 } | Select-Object -First 1
}
Add-Type -AssemblyName System.Windows.Forms
$instalado = Join-Path $raiz "programa"
Remove-Item -Recurse -Force $raiz -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $raiz | Out-Null

# Lo real que no se puede tocar: el estado del agente de verdad, su Inicio y su menú.
function Huella {
    $rutas = @("$env:LOCALAPPDATA\Morgan\agente\credencial.bin", "$env:LOCALAPPDATA\Morgan\agente\politica.json",
               "$env:LOCALAPPDATA\Morgan\agente\agente.json")
    $rutas += Get-ChildItem ([Environment]::GetFolderPath('Startup')), ([Environment]::GetFolderPath('Programs')) `
        -Filter "Morgan*.lnk" -ErrorAction SilentlyContinue |
        Where-Object Name -notlike "Morgan para Windows*" | ForEach-Object FullName   # el del programa, no
    $rutas | Where-Object { Test-Path $_ } | ForEach-Object {
        "$_|" + (Get-FileHash $_ -Algorithm SHA256).Hash
    }
}
$antes = Huella

$env:MORGAN_AGENTE_DIR = Join-Path $raiz "estado"
$env:MORGAN_CARPETA_INICIO = Join-Path $raiz "inicio"
$env:MORGAN_CARPETA_MENU = Join-Path $raiz "menu"
$resultado = [ordered]@{}
# El caso del 2026-10-04 (5.0.1): un PC que ya tiene el agente de la línea de PowerShell, con
# su política. Instalar y desinstalar el programa no puede llevárselo por delante.
New-Item -ItemType Directory -Force $env:MORGAN_AGENTE_DIR | Out-Null
'{"carpetas": ["C:/Users/ana/Documentos"]}' | Set-Content (Join-Path $env:MORGAN_AGENTE_DIR "politica.json")
try {
    $p = Start-Process $Instalador -ArgumentList @("/" + "S", "/D=$instalado") -PassThru -Wait
    $resultado.instalado = ($p.ExitCode -eq 0) -and (Test-Path "$instalado\Morgan.exe") `
        -and (Test-Path "$instalado\agente\morgan-agente\morgan-agente-fondo.exe")
    $clave = Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall |
        Where-Object { (Get-ItemProperty $_.PSPath).DisplayName -eq "Morgan para Windows" }
    $resultado.en_aplicaciones_instaladas = [bool]$clave
    # 5.1: arranca solo, en la bandeja, al iniciar sesión.
    $resultado.arranca_en_la_bandeja = "$((Get-ItemProperty $RUN -ErrorAction SilentlyContinue).'Morgan para Windows')" -like "*Morgan.exe*--bandeja"

    $estado = & "$instalado\agente\morgan-agente\morgan-agente.exe" estado 2>&1 | Select-Object -First 1
    $resultado.agente = "$estado"

    $app = Start-Process "$instalado\Morgan.exe" -PassThru
    Start-Sleep 6
    $ventana = VentanaDeMorgan $app.Id
    $resultado.ventana = [bool]$ventana
    # 5.1: cerrar la ventana la esconde (sigue en la bandeja), y abrirlo otra vez no lanza otro:
    # vuelve a enseñar la que había.
    if ($ventana) {
        $ventana.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).Close()
    }
    Start-Sleep 3
    $resultado.cerrar_la_ventana_la_esconde = (-not $app.HasExited) -and -not (VentanaDeMorgan $app.Id)
    $otra = Start-Process "$instalado\Morgan.exe" -PassThru
    Start-Sleep 4
    $resultado.una_sola_instancia = $otra.HasExited -and @(Get-Process Morgan -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$instalado*" }).Count -eq 1
    $resultado.abrirlo_otra_vez_la_enseña = [bool](VentanaDeMorgan $app.Id)
    # 5.2: Ctrl+Alt+M abre la conversación desde cualquier sitio, y otra vez la esconde.
    [System.Windows.Forms.SendKeys]::SendWait("^%m")
    $limite = (Get-Date).AddSeconds(15)
    while (-not (VentanaDeLaConversacion $app.Id) -and (Get-Date) -lt $limite) { Start-Sleep -Milliseconds 300 }
    $conversacion = VentanaDeLaConversacion $app.Id
    $resultado.el_atajo_abre_la_conversacion = [bool]$conversacion
    Start-Sleep 2
    # Con la conversación delante, la segunda pulsación la esconde (si no está delante, la
    # trae: es lo que se espera de un atajo). Aquí se pone delante, por si Windows no le dio
    # el primer plano a una ventana abierta desde una prueba.
    $resultado.la_conversacion_estaba_delante = "$($conversacion -and $conversacion.Current.HasKeyboardFocus)"
    if ($conversacion) { try { $conversacion.SetFocus() } catch { } }
    Start-Sleep 1
    [System.Windows.Forms.SendKeys]::SendWait("^%m")
    Start-Sleep 2
    $resultado.otra_vez_la_esconde = -not (VentanaDeLaConversacion $app.Id) -and -not $app.HasExited

    New-Item -ItemType Directory -Force (Join-Path $env:MORGAN_AGENTE_DIR "respaldos") | Out-Null
    "respaldo" | Set-Content (Join-Path $env:MORGAN_AGENTE_DIR "respaldos\r.txt")
    $p = Start-Process (Join-Path $instalado "uninstall.exe") -ArgumentList @("/" + "S") -PassThru -Wait
    Start-Sleep 5
    $resultado.desinstalado = ($p.ExitCode -eq 0) -and -not (Test-Path "$instalado\Morgan.exe")
    # Con el programa abierto en la bandeja: el desinstalador lo cierra.
    $resultado.sin_el_programa_abierto = -not @(Get-Process Morgan -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$instalado*" }).Count
    $resultado.fuera_del_inicio = -not "$((Get-ItemProperty $RUN -ErrorAction SilentlyContinue).'Morgan para Windows')"
    $resultado.fuera_de_aplicaciones = -not [bool](Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall |
        Where-Object { (Get-ItemProperty $_.PSPath).DisplayName -eq "Morgan para Windows" })
    $resultado.quedan_los_respaldos = Test-Path (Join-Path $env:MORGAN_AGENTE_DIR "respaldos\r.txt")
    $resultado.el_agente_de_la_linea_sigue = Test-Path (Join-Path $env:MORGAN_AGENTE_DIR "politica.json")
}
finally {
    # La ruta de instalación que recuerda el registro: si es la de la prueba, fuera. Si no, el
    # próximo instalador la propone: el 2026-10-04 mi programa de verdad acabó instalado dentro
    # de la carpeta de esta prueba (que la prueba borra al empezar).
    $claveDelPrograma = "HKCU:\Software\Morgan\Morgan para Windows"
    if ("$((Get-ItemProperty $claveDelPrograma -ErrorAction SilentlyContinue).'(default)')" -like "$raiz*") {
        Remove-Item $claveDelPrograma -Recurse -Force
    }
    Get-Process Morgan -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$instalado*" } | Stop-Process -Force
    Remove-Item Env:MORGAN_AGENTE_DIR, Env:MORGAN_CARPETA_INICIO, Env:MORGAN_CARPETA_MENU -ErrorAction SilentlyContinue
    $despues = Huella
    $resultado.lo_real_intacto = -not (Compare-Object @($antes) @($despues))
    Remove-Item -Recurse -Force $raiz -ErrorAction SilentlyContinue
}
$resultado.GetEnumerator() | ForEach-Object { "{0,-28} {1}" -f $_.Key, $_.Value }
if (-not $resultado.lo_real_intacto) { Write-Error "CAMBIÓ ALGO REAL DEL AGENTE: revisar ya." }
$fallos = @($resultado.GetEnumerator() | Where-Object { $_.Value -is [bool] -and -not $_.Value } | ForEach-Object Key)
if ($fallos) { Write-Error "Falla: $($fallos -join ', ')" }
