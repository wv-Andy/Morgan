// Las órdenes del programa, declaradas (5.3): así **ninguna** se puede pedir sin un permiso
// explícito en `capabilities/`. Sin esta lista, Tauri deja usar todas a cualquier página local.
const ORDENES: &[&str] = &[
    "estado",
    "resumen",
    "pausar",
    "reanudar",
    "ultimas",
    "consultar",
    "emparejar",
    "desemparejar",
    "abrir_ajustes",
    "abrir_web",
    "pantalla_emparejar",
    "programa",
    "actualizar_ahora",
];

fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(ORDENES)),
    )
    .expect("no se pudo preparar el programa");
}
