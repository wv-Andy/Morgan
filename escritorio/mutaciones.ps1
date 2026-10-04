<#
Las pruebas del estado de la bandeja (src-tauri/src/bandeja.rs), y que cazan lo que tienen que
cazar: cada mutación rompe el código a propósito y las pruebas tienen que fallar. Con `rustc
--test`, sin compilar Tauri: segundos. Lo corre .github/workflows/escritorio.yml.

    pwsh escritorio/mutaciones.ps1
#>
$ErrorActionPreference = "Stop"
$fuente = Join-Path $PSScriptRoot "src-tauri/src/bandeja.rs"
$tmp = Join-Path ([IO.Path]::GetTempPath()) "morgan-bandeja"
New-Item -ItemType Directory -Force $tmp | Out-Null

function Probar([string]$codigo) {
    $rs = Join-Path $tmp "bandeja.rs"
    $exe = Join-Path $tmp "bandeja.exe"
    Set-Content -Path $rs -Value $codigo -Encoding utf8NoBOM
    & rustc --edition 2021 --test $rs -o $exe 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { return "no compila" }
    & $exe -q 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { return "falla" }
    return "pasa"
}

$original = Get-Content $fuente -Raw
$r = Probar $original
if ($r -ne "pasa") { throw "Las pruebas de la bandeja no pasan sin mutar: $r" }
Write-Output "sin mutar: pasa"

$mutaciones = [ordered]@{
    "el pulso no caduca"             = @("if ahora - m <= PULSO_VIGENTE", "if true")
    "la pausa no manda"              = @("    if l.en_pausa {`n        return Clave::EnPausa;", "    if false {`n        return Clave::EnPausa;")
    "sin emparejar no manda"         = @("    if !l.emparejado {", "    if false {")
    "revocado solo con pulso"        = @("Some(""REVOKED"") | Some(""INCOMPATIBLE"") | Some(""OUTDATED"") => return Clave::Revocado,", "")
    "rendido no se dice"             = @("Some(""RENDIDO"") => return Clave::Rendido,", "")
    "el arranque no cuenta"          = @("&& !reciente(l.arrancado, ahora)", "")
    "ready sin pulso propio"         = @("if l.estado == Some(""READY"") && reciente(l.pulso, ahora)", "if l.estado == Some(""READY"")")
    "cualquier estado es conectado"  = @("if l.estado == Some(""READY"") && reciente(l.pulso, ahora)", "if reciente(l.pulso, ahora)")
    "color siempre"                  = @("    c == Clave::Conectado`n", "    c != Clave::SinEmparejar`n")
    "reanudar en todo"               = @("        Clave::EnPausa => (false, true),", "        Clave::EnPausa => (true, true),")
}
$vivas = 0
foreach ($nombre in $mutaciones.Keys) {
    $de, $a = $mutaciones[$nombre]
    $texto = $original -replace "`r`n", "`n"
    if (-not $texto.Contains($de)) { Write-Output "$nombre : NO APLICADA"; $vivas++; continue }
    $r = Probar ($texto.Replace($de, $a))
    if ($r -eq "pasa") { Write-Output "$nombre : VIVA"; $vivas++ } else { Write-Output "$nombre : cazada ($r)" }
}
if ($vivas) { throw "$vivas mutaciones sin cazar" }
Write-Output "Todas cazadas."
exit 0   # el último $LASTEXITCODE es el de una mutación, que falla a propósito
