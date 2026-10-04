<#
Actualizar Morgan para Windows encima de la 5.0.1 sin desemparejar el agente (5.1).

Por el camino de quien hace doble clic: el instalador con ventana, que ofrece «Desinstalar
antes de instalar» marcado por defecto y ejecuta el desinstalador viejo, cuyo gancho
desemparejaba el agente que emparejó el programa. Los botones se pulsan por accesibilidad, como
los pulsaría una persona (Siguiente, Desinstalar, Instalar, Terminar).

Con un agente de la 5.0.1 emparejado de mentira (contra una nube que no existe) y en marcha:
después de actualizar, su estado tiene que estar intacto, la marca de vuelta y el agente
arrancado desde la versión nueva. Todo aislado (estado, Inicio y menú); en un PC con el
programa instalado de verdad no corre. Lo corre .github/workflows/escritorio.yml.

    powershell -ExecutionPolicy Bypass -File empaquetado\probar_actualizar.ps1 -Instalador <nuevo.exe>
#>
param([Parameter(Mandatory = $true)][string]$Instalador,
      # Además, cambiar la carpeta en la página de la carpeta (como sacar el programa de donde
      # no debía estar: el 2026-10-04 el mío acabó en la carpeta de una prueba).
      [switch]$Mover,
      [string]$Anterior = "https://github.com/wv-Andy/Morgan/releases/download/v5.0.1/Morgan.para.Windows_5.0.1_x64-setup.exe")

$ErrorActionPreference = "Stop"
$CLAVE = "HKCU:\Software\Morgan\Morgan para Windows"
$instaladoDeVerdad = [bool](Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall -ErrorAction SilentlyContinue |
    Where-Object { (Get-ItemProperty $_.PSPath).DisplayName -eq "Morgan para Windows" })
if ($instaladoDeVerdad -and $env:GITHUB_ACTIONS -ne "true") {
    throw "Morgan para Windows está instalado en este PC: la prueba se llevaría el de verdad."
}

$raiz = Join-Path $env:USERPROFILE "morgan-prueba-actualizar"
$instalado = Join-Path $raiz "programa"
$destino = if ($Mover) { Join-Path $raiz "otra carpeta\Morgan para Windows" } else { $instalado }
Remove-Item -Recurse -Force $raiz -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $raiz | Out-Null
$env:MORGAN_AGENTE_DIR = Join-Path $raiz "estado"
$env:MORGAN_CARPETA_INICIO = Join-Path $raiz "inicio"
$env:MORGAN_CARPETA_MENU = Join-Path $raiz "menu"
$env:PYTHONIOENCODING = "utf-8"
$agente = Join-Path $instalado "agente\morgan-agente\morgan-agente.exe"

# Los diálogos de NSIS, con la API de siempre de Windows: el botón que hace avanzar (Siguiente,
# Desinstalar, Instalar, Terminar) es siempre el control 1 del diálogo. UI Automation veía la
# ventana pero ninguno de sus botones (medido en GitHub).
Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class Dialogo {
    [DllImport("user32.dll")] static extern IntPtr GetDlgItem(IntPtr h, int id);
    [DllImport("user32.dll")] static extern bool IsWindowEnabled(IntPtr h);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] static extern IntPtr PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, string l);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] static extern bool IsHungAppWindow(IntPtr h);
    delegate bool Visitar(IntPtr h, IntPtr l);
    public static string Como(IntPtr h) {
        return (IsWindowEnabled(h) ? "activa" : "DESHABILITADA") + (IsHungAppWindow(h) ? ", COLGADA" : "");
    }
    [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr h, Visitar f, IntPtr l);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, StringBuilder l);
    // Con WM_GETTEXT: GetWindowText no lee el texto de un campo de otro proceso (medido: la
    // página de la carpeta no se encontraba nunca).
    public static string Texto(IntPtr h) { var s = new StringBuilder(1024); SendMessage(h, 0x000D /* WM_GETTEXT */, (IntPtr)1024, s); return s.ToString(); }
    public static string Textos(IntPtr h) {
        var t = new List<string>();
        EnumChildWindows(h, (c, l) => { if (IsWindowVisible(c)) { var x = Texto(c); if (x.Length > 0) t.Add(x); } return true; }, IntPtr.Zero);
        return string.Join(" / ", t);
    }
    // Desmarca «Ejecutar Morgan para Windows» de la última página: la prueba no deja el programa
    // abierto. Devuelve si había que desmarcar algo.
    public static bool SinEjecutar(IntPtr h) {
        bool hubo = false;
        EnumChildWindows(h, (c, l) => {
            if (Texto(c).Replace("&", "").StartsWith("Ejecutar")) {
                SendMessage(c, 0x00F1 /* BM_SETCHECK */, IntPtr.Zero, IntPtr.Zero);
                hubo = true;
            }
            return true;
        }, IntPtr.Zero);
        return hubo;
    }
    // En la página de la carpeta (la que tiene un campo con una ruta): que diga `a`. Devuelve si
    // lo cambió (si ya decía `a`, no).
    public static bool CambiarCarpeta(IntPtr h, string a) {
        bool hecho = false;
        EnumChildWindows(h, (c, l) => {
            var clase = new StringBuilder(64); GetClassName(c, clase, 64);
            var texto = Texto(c);
            if (clase.ToString() == "Edit" && texto.Contains(":\\") && IsWindowVisible(c) &&
                !string.Equals(texto.TrimEnd('\\'), a.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase)) {
                SendMessage(c, 0x000C /* WM_SETTEXT */, IntPtr.Zero, a);
                hecho = true;
            }
            return true;
        }, IntPtr.Zero);
        return hecho;
    }
    // Pulsa el control 1 si se puede; devuelve su texto, o null.
    public static string Avanzar(IntPtr ventana) {
        var b = GetDlgItem(ventana, 1);
        if (b == IntPtr.Zero || !IsWindowVisible(b) || !IsWindowEnabled(b)) return null;
        // Al diálogo, como lo haría el botón (WM_COMMAND, BN_CLICKED del control 1): BM_CLICK
        // simula el ratón y se perdía en la última página (medido en GitHub).
        PostMessage(ventana, 0x0111 /* WM_COMMAND */, (IntPtr)1, b);
        return Texto(b);
    }
}
"@
$pulsados = New-Object System.Collections.Generic.List[string]
$vistos = New-Object System.Collections.Generic.HashSet[string]
$errores = New-Object System.Collections.Generic.List[string]
$idInstalador = 0
$ultimo = @{}

function Pulsar {
    # El instalador y su desinstalador viejo (que corre desde la carpeta instalada).
    foreach ($proceso in @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
            $_.Id -eq $idInstalador -or $_.Path -like "$raiz*" })) {
        $h = $proceso.MainWindowHandle
        if ($h -eq [IntPtr]::Zero) { continue }
        $textos = [Dialogo]::Textos($h)
        $null = $vistos.Add("$($proceso.ProcessName): $([Dialogo]::Texto($h)) [$textos]")
        # La misma página que la última vez: no se vuelve a pulsar enseguida (tarda en cambiar),
        # pero sí pasados 5 s: un BM_CLICK puede perderse si la ventana no está activa (medido:
        # «Terminar» pulsado una vez y el instalador seguía abierto 4 minutos después).
        $antes = $ultimo[$proceso.Id]
        if ($antes -and $antes[0] -eq $textos -and ((Get-Date) - $antes[1]).TotalSeconds -lt 5) { continue }
        if ([Dialogo]::SinEjecutar($h)) { $pulsados.Add("$($proceso.ProcessName): sin ejecutar") }
        if ($Mover -and $proceso.Id -eq $idInstalador -and [Dialogo]::CambiarCarpeta($h, $destino)) {
            $pulsados.Add("$($proceso.ProcessName): carpeta cambiada")
            Start-Sleep -Milliseconds 500
            $textos = [Dialogo]::Textos($h)
        }
        $pulsado = [Dialogo]::Avanzar($h)
        if ($pulsado) {
            $ultimo[$proceso.Id] = @($textos, (Get-Date))
            $pulsados.Add("$([int]((Get-Date) - $empieza).TotalSeconds)s $($proceso.ProcessName): $($pulsado -replace '&', '') ($([Dialogo]::Como($h)))")
        }
    }
}

function Huella {
    @("agente.json", "credencial.bin", "politica.json") | ForEach-Object {
        $f = Join-Path $env:MORGAN_AGENTE_DIR $_
        if (Test-Path $f) { "$_|" + (Get-FileHash $f -Algorithm SHA256).Hash } else { "$_|falta" }
    }
}
function EnMarcha([string]$exe) { [bool]((& $exe estado) -match "En marcha ahora: sí") }

$resultado = [ordered]@{}
try {
    $viejo = Join-Path $raiz "anterior.exe"
    Invoke-WebRequest $Anterior -OutFile $viejo -UseBasicParsing
    $p = Start-Process $viejo -ArgumentList @("/" + "S", "/D=$instalado") -PassThru -Wait
    $resultado.la_501_instalada = ($p.ExitCode -eq 0) -and (Test-Path $agente)

    # Emparejado por el programa (con su marca), con carpetas, y en marcha.
    python -c @"
import json, time
from src.agente import estado as a
from src.agente.politica import Politica
a.guardar(a.Emparejamiento('agt-prueba', 'http://127.0.0.1:9', 'CI', 'ci@ejemplo.com', time.time()), 'mga_prueba')
p = Politica(); p.anadir(r'$raiz'); p.guardar()
(a.carpeta() / 'programa.json').write_text(json.dumps({'programa': 'prueba', 'desde': time.time()}), encoding='utf-8')
"@
    if ($LASTEXITCODE -ne 0) { throw "no se pudo preparar el estado" }
    Start-Process (Join-Path $instalado "agente\morgan-agente\morgan-agente-fondo.exe") -ArgumentList "vigilar" | Out-Null
    $limite = (Get-Date).AddSeconds(30)
    while (-not (EnMarcha $agente) -and (Get-Date) -lt $limite) { Start-Sleep -Milliseconds 300 }
    $resultado.la_501_en_marcha = EnMarcha $agente
    $antes = Huella

    # El nuevo, con ventana y sin /D: propone la carpeta de antes, como al hacer doble clic.
    $empieza = Get-Date
    $nuevo = Start-Process $Instalador -PassThru
    $null = $nuevo.Handle      # sin pedirlo al principio, Windows PowerShell pierde cómo acabó
    $idInstalador = $nuevo.Id
    $limite = (Get-Date).AddSeconds(240)
    while (-not $nuevo.HasExited -and (Get-Date) -lt $limite) {
        try { Pulsar } catch { $errores.Add($_.Exception.Message) }
        Start-Sleep -Milliseconds 1500
    }
    $resultado.instalador_acabo = $nuevo.WaitForExit(30000)
    $resultado.segundos = [int]((Get-Date) - $empieza).TotalSeconds
    $resultado.corrio_el_desinstalador_viejo = [bool]($pulsados | Where-Object { $_ -like "*uninstall: Desinstalar*" })

    $despues = Huella
    $resultado.estado_intacto = -not (Compare-Object @($antes) @($despues))
    $resultado.marca_de_vuelta = (Test-Path (Join-Path $env:MORGAN_AGENTE_DIR "programa.json")) -and
        -not (Test-Path (Join-Path $env:MORGAN_AGENTE_DIR "programa.json.actualizando"))
    $agenteNuevo = Join-Path $destino "agente\morgan-agente\morgan-agente.exe"
    $resultado.version_nueva = "$(& $agenteNuevo version)".Trim()
    $limite = (Get-Date).AddSeconds(30)
    while (-not (EnMarcha $agenteNuevo) -and (Get-Date) -lt $limite) { Start-Sleep -Milliseconds 300 }
    $resultado.en_marcha_tras_actualizar = EnMarcha $agenteNuevo
    # El agente que corre es el de la carpeta nueva, y el acceso de Inicio apunta a ella.
    $resultado.corre_desde_la_carpeta_nueva = [bool](Get-Process morgan-agente* -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$destino*" })
    $acceso = Get-ChildItem $env:MORGAN_CARPETA_INICIO -Filter *.lnk -ErrorAction SilentlyContinue | Select-Object -First 1
    $resultado.inicio_apunta_a_la_nueva = $acceso -and
        ((New-Object -ComObject WScript.Shell).CreateShortcut($acceso.FullName).TargetPath -like "$destino*")
    if ($Mover) { $resultado.la_vieja_ya_no_esta = -not (Test-Path (Join-Path $instalado "Morgan.exe")) }
}
finally {
    if ($nuevo -and -not $nuevo.HasExited) { Stop-Process -Id $nuevo.Id -Force -ErrorAction SilentlyContinue }
    Get-Process Morgan, morgan-agente* -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$raiz*" } | Stop-Process -Force
    foreach ($carpeta in @($destino, $instalado) | Select-Object -Unique) {
        if (Test-Path (Join-Path $carpeta "uninstall.exe")) {
            Start-Process (Join-Path $carpeta "uninstall.exe") -ArgumentList @("/" + "S") -Wait
        }
    }
    # La ruta de instalación que recuerda el registro: si es la de la prueba, fuera (si no, el
    # próximo instalador la propondría; pasó en mi PC con la prueba de emparejar).
    if ("$((Get-ItemProperty $CLAVE -ErrorAction SilentlyContinue).'(default)')" -like "$raiz*") {
        Remove-Item $CLAVE -Recurse -Force
    }
    Remove-Item Env:MORGAN_AGENTE_DIR, Env:MORGAN_CARPETA_INICIO, Env:MORGAN_CARPETA_MENU -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $raiz -ErrorAction SilentlyContinue
}
$resultado.GetEnumerator() | ForEach-Object { "{0,-30} {1}" -f $_.Key, $_.Value }
"Pulsado: " + ($pulsados -join " | ")
"Visto: " + ($vistos -join " || ")
"Errores: " + (($errores | Select-Object -Unique) -join " || ")
$fallos = @($resultado.GetEnumerator() | Where-Object { $_.Value -is [bool] -and -not $_.Value } | ForEach-Object Key)
if ($fallos) { Write-Error "Falla: $($fallos -join ', ')" }
