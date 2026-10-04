<#
La pausa de la bandeja (5.1), medida con el agente congelado de verdad: lo que tarda desde que
se pulsa «Pausar» (lanzar `morgan-agente.exe pausar`, como hace el programa) hasta que el agente
y su vigilante ya no corren. El criterio: menos de 2 s. Y que la pausa aguanta: un vigilante
lanzado después (como al iniciar sesión) no levanta el agente, y `reanudar` sí.

Todo aislado (estado, Inicio y menú en una carpeta temporal); el emparejamiento es de mentira,
contra una nube que no existe: el agente se queda reconectando, que es estar en marcha. Lo
corre .github/workflows/escritorio.yml.

    pwsh empaquetado/probar_pausa.ps1 -Agente escritorio/src-tauri/agente/morgan-agente
#>
param([Parameter(Mandatory = $true)][string]$Agente)

$ErrorActionPreference = "Stop"
$exe = Join-Path (Resolve-Path $Agente) "morgan-agente.exe"
$fondo = Join-Path (Resolve-Path $Agente) "morgan-agente-fondo.exe"
$raiz = Join-Path ([IO.Path]::GetTempPath()) "morgan-prueba-pausa"
Remove-Item -Recurse -Force $raiz -ErrorAction SilentlyContinue
$env:MORGAN_AGENTE_DIR = Join-Path $raiz "estado"
$env:MORGAN_CARPETA_INICIO = Join-Path $raiz "inicio"
$env:MORGAN_CARPETA_MENU = Join-Path $raiz "menu"
$env:PYTHONIOENCODING = "utf-8"

function EnMarcha { [bool]((& $exe estado) -match "En marcha ahora: sí") }
function Esperar([scriptblock]$cond, [double]$segundos) {
    $limite = (Get-Date).AddSeconds($segundos)
    while (-not (& $cond) -and (Get-Date) -lt $limite) { Start-Sleep -Milliseconds 200 }
    return (& $cond)
}

$resultado = [ordered]@{}
try {
    python -c "import time; from src.agente import estado as a; a.guardar(a.Emparejamiento('agt-prueba', 'http://127.0.0.1:9', 'CI', 'ci@ejemplo.com', time.time()), 'mga_prueba')"
    if ($LASTEXITCODE -ne 0) { throw "no se pudo crear el emparejamiento de prueba" }

    Start-Process $fondo -ArgumentList "vigilar" | Out-Null
    $resultado.arranca = Esperar { EnMarcha } 30

    $tiempo = Measure-Command { $script:dice = & $exe pausar }
    $resultado.pausa_ms = [int]$tiempo.TotalMilliseconds
    $resultado.dice = "$dice"
    $resultado.en_menos_de_2_s = $tiempo.TotalSeconds -lt 2
    $resultado.parado = -not (EnMarcha)
    $resultado.sin_procesos = -not @(Get-Process morgan-agente* -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$((Resolve-Path $Agente).Path)*" }).Count

    # Como al iniciar sesión: el acceso de Inicio lanza el vigilante. En pausa, no levanta nada.
    $v = Start-Process $fondo -ArgumentList "vigilar" -PassThru
    $resultado.la_pausa_aguanta = $v.WaitForExit(15000) -and -not (EnMarcha)

    & $exe reanudar | Out-Null
    $resultado.reanuda = Esperar { EnMarcha } 30
}
finally {
    & $exe pausar | Out-Null
    & $exe parar | Out-Null
    Remove-Item Env:MORGAN_AGENTE_DIR, Env:MORGAN_CARPETA_INICIO, Env:MORGAN_CARPETA_MENU -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $raiz -ErrorAction SilentlyContinue
}
$resultado.GetEnumerator() | ForEach-Object { "{0,-18} {1}" -f $_.Key, $_.Value }
$fallos = @($resultado.GetEnumerator() | Where-Object { $_.Value -is [bool] -and -not $_.Value } | ForEach-Object Key)
if ($fallos) { throw "Falla: $($fallos -join ', ')" }
