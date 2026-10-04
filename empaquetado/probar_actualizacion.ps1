<#
Las actualizaciones de Morgan para Windows (5.3), de punta a punta, en GitHub Actions.

1. **Actualizar**: se publica una versión de prueba (prerelease, que se borra al acabar) con
   un instalador **9.9.9** compilado aquí mismo (el mismo programa, con otro número), su firma y
   su `latest.json`, más el de la versión de ahora (la vuelta atrás). El programa, instalado y
   con `--actualizar`, la busca por https en GitHub, comprueba la firma, guarda el instalador de
   su propia versión (también con su firma comprobada) y la instala; el instalador lo vuelve a
   abrir, ya en la 9.9.9, y se juzga. Un `latest.json` que anuncia 9.9.9 con el instalador de
   otra versión no vale: el actualizador lo rechaza como manipulado (medido: así era la primera
   versión de esta prueba).
2. **Volver atrás**: con el PC sin conectar (emparejado contra una nube que no existe) y una marca
   de actualización recién hecha desde la 5.2.0 (firmada aquí con la clave de verdad), pasado el
   plazo el programa reinstala la 5.2.0.
3. **No volver a algo sin firma válida**: el mismo caso con el instalador anterior manipulado.
4. **Confirmar**: si antes de actualizar el PC no estaba conectado, no se castiga a la nueva.

Todo aislado (estado, Inicio y menú del agente en una carpeta de prueba). En un PC con el
programa instalado de verdad no corre.

    powershell -ExecutionPolicy Bypass -File empaquetado\probar_actualizacion.ps1 -Instalador <.exe>
#>
param([Parameter(Mandatory = $true)][string]$Instalador,
      [Parameter(Mandatory = $true)][string]$NuevoInstalador)

$ErrorActionPreference = "Stop"
$instaladoDeVerdad = [bool](Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall -ErrorAction SilentlyContinue |
    Where-Object { (Get-ItemProperty $_.PSPath).DisplayName -eq "Morgan para Windows" })
if ($instaladoDeVerdad -or $env:GITHUB_ACTIONS -ne "true") {
    throw "Esta prueba solo corre en GitHub Actions, en una máquina sin Morgan para Windows."
}

$Instalador = (Resolve-Path $Instalador).Path
$NuevoInstalador = (Resolve-Path $NuevoInstalador).Path
$version = [regex]::Match((Split-Path $Instalador -Leaf), "_(\d+\.\d+\.\d+)_x64").Groups[1].Value
$nueva = [regex]::Match((Split-Path $NuevoInstalador -Leaf), "_(\d+\.\d+\.\d+)_x64").Groups[1].Value
$raiz = Join-Path $env:USERPROFILE "morgan-prueba-actualizacion"
$instalado = Join-Path $raiz "programa"
$datos = Join-Path $env:LOCALAPPDATA "io.github.wv-andy.morgan\actualizacion"
$CLAVE = "HKCU:\Software\Morgan\Morgan para Windows"
Remove-Item -Recurse -Force $raiz, $datos -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $raiz, $datos | Out-Null
$env:MORGAN_AGENTE_DIR = Join-Path $raiz "estado"
$env:MORGAN_CARPETA_INICIO = Join-Path $raiz "inicio"
$env:MORGAN_CARPETA_MENU = Join-Path $raiz "menu"
$repo = "wv-Andy/Morgan"
$tag = "prueba-actualizar-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT"
$log = Join-Path $datos "actualizacion.log"

function Version {
    $c = Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall -ErrorAction SilentlyContinue |
        Where-Object { (Get-ItemProperty $_.PSPath).DisplayName -eq "Morgan para Windows" } | Select-Object -First 1
    if ($c) { (Get-ItemProperty $c.PSPath).DisplayVersion } else { "" }
}
function Esperar([scriptblock]$cond, [int]$segundos) {
    $limite = (Get-Date).AddSeconds($segundos)
    while (-not (& $cond) -and (Get-Date) -lt $limite) { Start-Sleep -Seconds 2 }
    return [bool](& $cond)
}
function Log { if (Test-Path $log) { Get-Content $log -Raw -Encoding UTF8 } else { "" } }
function Parar {
    Get-Process Morgan, morgan-agente*, *setup* -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$raiz*" -or $_.Path -like "$datos*" -or $_.Path -like "$env:TEMP*" } |
        Stop-Process -Force -ErrorAction SilentlyContinue
}
function Instalar([string]$exe) {
    Parar
    $p = Start-Process $exe -ArgumentList @("/" + "S", "/D=$instalado") -PassThru -Wait
    if ($p.ExitCode -ne 0) { throw "no se instaló $exe" }
}
function Marca([string]$de, [string]$anterior, [bool]$conectado) {
    $m = @{ de = $de; a = $version; desde = [double][DateTimeOffset]::UtcNow.ToUnixTimeSeconds();
            antes_conectado = $conectado; anterior = $anterior; volviendo = $false }
    [IO.File]::WriteAllText((Join-Path $datos "actualizacion.json"), ($m | ConvertTo-Json -Compress))
}

$resultado = [ordered]@{ version = $version }
try {
    # Emparejado de mentira contra una nube que no existe: el PC no se conecta nunca.
    python -c "import time; from src.agente import estado as a; a.guardar(a.Emparejamiento('agt-prueba', 'http://127.0.0.1:9', 'CI', 'ci@ejemplo.com', time.time()), 'mga_prueba')"
    if ($LASTEXITCODE -ne 0) { throw "no se pudo preparar el estado" }

    # --- 1. Actualizar, por GitHub y con firma ------------------------------------------------
    $publicar = Join-Path $raiz "publicar"
    New-Item -ItemType Directory -Force $publicar | Out-Null
    $nombre = "Morgan.para.Windows_${version}_x64-setup.exe"
    $nombreNueva = "Morgan.para.Windows_${nueva}_x64-setup.exe"
    Copy-Item $Instalador (Join-Path $publicar $nombre)
    Copy-Item "$Instalador.sig" (Join-Path $publicar "$nombre.sig")
    Copy-Item $NuevoInstalador (Join-Path $publicar $nombreNueva)
    Copy-Item "$NuevoInstalador.sig" (Join-Path $publicar "$nombreNueva.sig")
    $latest = @{ version = $nueva; notes = "prueba"; pub_date = (Get-Date).ToUniversalTime().ToString("o");
                 platforms = @{ "windows-x86_64" = @{
                     signature = (Get-Content "$NuevoInstalador.sig" -Raw).Trim();
                     url = "https://github.com/$repo/releases/download/$tag/$nombreNueva" } } }
    [IO.File]::WriteAllText((Join-Path $publicar "latest.json"), ($latest | ConvertTo-Json -Depth 5))
    gh release create $tag -R $repo --prerelease --title "Prueba de actualización (se borra sola)" `
        --notes "Creada y borrada por la integración continua." `
        (Join-Path $publicar $nombre) (Join-Path $publicar "$nombre.sig") `
        (Join-Path $publicar $nombreNueva) (Join-Path $publicar "$nombreNueva.sig") (Join-Path $publicar "latest.json")
    if ($LASTEXITCODE -ne 0) { throw "no se pudo publicar la versión de prueba" }

    Instalar $Instalador
    $env:MORGAN_ACTUALIZACIONES = "https://github.com/$repo/releases/download/$tag/latest.json"
    $env:MORGAN_VERSIONES_ANTERIORES = "https://github.com/$repo/releases/download/$tag"
    Start-Process (Join-Path $instalado "Morgan.exe") -ArgumentList "--bandeja", "--actualizar" | Out-Null
    $resultado.actualiza = Esperar { (Log) -match "actualizando de la $version a la $([regex]::Escape($nueva))" } 180
    $resultado.guarda_la_anterior_comprobada = Test-Path (Join-Path $datos "$nombre.sig")
    $resultado.instala_la_nueva = Esperar { (Version) -eq $nueva } 240
    # El instalador la vuelve a abrir (/R), y la nueva se juzga: el PC no estaba conectado antes
    # (emparejado contra una nube que no existe), así que no hay con qué comparar y se confirma.
    $resultado.la_nueva_se_abre_y_se_juzga = Esperar { (Log) -match "la $([regex]::Escape($nueva)) se confirma" } 180
    if (-not ($resultado.instala_la_nueva -and $resultado.la_nueva_se_abre_y_se_juzga)) {
        # Para saber por qué: si el instalador lo abrió (lo hace con el usuario de la sesión, a
        # través de explorer), con qué argumentos, y qué dice el registro.
        "--- tras actualizar: procesos y registro"
        Get-CimInstance Win32_Process | Where-Object { $_.Name -match "Morgan|setup|explorer" } |
            ForEach-Object { "{0} {1}" -f $_.ProcessId, $_.CommandLine }
        Log
    }
    Remove-Item Env:MORGAN_ACTUALIZACIONES, Env:MORGAN_VERSIONES_ANTERIORES -ErrorAction SilentlyContinue

    # De vuelta a la versión de ahora para lo demás: desinstalar la 9.9.9 e instalar la de ahora.
    Parar
    Start-Process (Join-Path $instalado "uninstall.exe") -ArgumentList @("/" + "S") -Wait
    Instalar $Instalador
    $resultado.de_vuelta_en_la_de_ahora = (Version) -eq $version

    # --- 2. Volver atrás a la 5.2.0, firmada aquí con la clave de verdad ----------------------
    Parar
    Remove-Item $log, (Join-Path $datos "actualizacion.json") -ErrorAction SilentlyContinue
    $anterior = Join-Path $datos "Morgan.para.Windows_5.2.0_x64-setup.exe"
    Invoke-WebRequest "https://github.com/$repo/releases/download/v5.2.0/Morgan.para.Windows_5.2.0_x64-setup.exe" `
        -OutFile $anterior -UseBasicParsing
    Push-Location (Join-Path $PSScriptRoot "..\escritorio")
    npx tauri signer sign $anterior | Out-Null
    Pop-Location
    if (-not (Test-Path "$anterior.sig")) { throw "no se pudo firmar la anterior" }
    Marca "5.2.0" $anterior $true
    $env:MORGAN_PLAZO_ESTRENO = "20"
    Start-Process (Join-Path $instalado "Morgan.exe") -ArgumentList "--bandeja" | Out-Null
    $resultado.sin_conectar_vuelve_a_la_anterior = Esperar { (Version) -eq "5.2.0" } 180
    $resultado.y_dice_por_que = (Log) -match "no conectó el PC"

    # --- 3. Con el instalador anterior manipulado, no se vuelve ------------------------------
    Instalar $Instalador
    Remove-Item $log, (Join-Path $datos "actualizacion.json") -ErrorAction SilentlyContinue
    $manipulado = Join-Path $datos "manipulado-setup.exe"
    Copy-Item $anterior $manipulado
    Copy-Item "$anterior.sig" "$manipulado.sig"
    [IO.File]::AppendAllText($manipulado, "x")
    Marca "5.2.0" $manipulado $true
    Start-Process (Join-Path $instalado "Morgan.exe") -ArgumentList "--bandeja" | Out-Null
    $resultado.no_vuelve_a_algo_manipulado = (Esperar { (Log) -match "no se puede comprobar" } 120) -and ((Version) -eq $version)

    # --- 4. Sin red antes de actualizar, la nueva se confirma --------------------------------
    Parar
    Remove-Item $log -ErrorAction SilentlyContinue
    Marca "5.2.0" $anterior $false
    Start-Process (Join-Path $instalado "Morgan.exe") -ArgumentList "--bandeja" | Out-Null
    $resultado.sin_red_antes_se_confirma = (Esperar { (Log) -match "se confirma" } 60) -and
        -not (Test-Path (Join-Path $datos "actualizacion.json")) -and ((Version) -eq $version)
}
finally {
    gh release delete $tag -R $repo --yes --cleanup-tag 2>$null
    Parar
    if (Test-Path (Join-Path $instalado "uninstall.exe")) {
        Start-Process (Join-Path $instalado "uninstall.exe") -ArgumentList @("/" + "S") -Wait
    }
    if ("$((Get-ItemProperty $CLAVE -ErrorAction SilentlyContinue).'(default)')" -like "$raiz*") {
        Remove-Item $CLAVE -Recurse -Force
    }
    Remove-Item Env:MORGAN_AGENTE_DIR, Env:MORGAN_CARPETA_INICIO, Env:MORGAN_CARPETA_MENU, Env:MORGAN_PLAZO_ESTRENO -ErrorAction SilentlyContinue
}
$resultado.GetEnumerator() | ForEach-Object { "{0,-34} {1}" -f $_.Key, $_.Value }
"--- actualizacion.log:"
Log
$fallos = @($resultado.GetEnumerator() | Where-Object { $_.Value -is [bool] -and -not $_.Value } | ForEach-Object Key)
if ($fallos) { Write-Error "Falla: $($fallos -join ', ')" }
