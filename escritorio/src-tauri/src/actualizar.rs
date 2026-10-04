// Las actualizaciones de Morgan para Windows (5.3). Decisiones del 2026-10-04: avisa y se
// instala **al pulsar** «Actualizar»; la clave que firma las actualizaciones vive como secreto
// del repositorio público (y una copia en el PC de quien publica); y **vuelta atrás
// automática**: si la versión nueva no vuelve a conectar el PC en 3 minutos despierto, se
// reinstala la anterior.
//
// Cómo:
// 1. `buscar`: el actualizador de Tauri lee `latest.json` de la última versión publicada y
//    comprueba la firma de lo que descarga con la clave pública que lleva el programa.
// 2. `actualizar`: antes de instalar, baja el instalador **de la versión que corre** y su firma,
//    la comprueba con la misma clave y lo guarda; deja una marca (`actualizacion.json`) con de
//    qué versión a cuál, si el PC estaba conectado y dónde está la anterior; e instala.
// 3. `juzgar`, al arrancar la versión nueva: mira el estado del PC cada 5 s y aplica
//    `bandeja::veredicto`. Confirmada, borra la marca; si no, reinstala la anterior (otra vez
//    con su firma comprobada) y la anterior, al arrancar, lo dice.

use std::io::Read;
use std::path::PathBuf;
use std::process::Command;
use std::time::{Duration, Instant};

use base64::Engine;
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};
use tauri_plugin_updater::UpdaterExt;

use crate::bandeja::{self, Clave, Veredicto};

/// La clave pública de las actualizaciones (la misma que `plugins.updater.pubkey` en
/// `tauri.conf.json`; una prueba comprueba que coinciden).
pub const CLAVE_PUBLICA: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IERBNjM2RjNBNjUxNDk2NEIKUldSTGxoUmxPbTlqMnVqZUN3WHFpQjBLbTBacHZsTnNUWjJYSkI5RXpkMXRvcFRlNmg2aEJHUTgK";
/// Dónde se publican las versiones.
const VERSIONES: &str = "https://github.com/wv-Andy/Morgan/releases";

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Marca {
    pub de: String,
    pub a: String,
    pub desde: f64,
    pub antes_conectado: bool,
    /// El instalador de la versión anterior, comprobado. `None`: no se pudo preparar.
    pub anterior: Option<String>,
    /// Ya se está volviendo a la anterior (para no hacerlo en bucle).
    #[serde(default)]
    pub volviendo: bool,
}

fn carpeta(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_local_data_dir()
        .map(|d| d.join("actualizacion"))
        .map_err(|e| e.to_string())
}

fn ruta_marca(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(carpeta(app)?.join("actualizacion.json"))
}

pub fn leer_marca(app: &AppHandle) -> Option<Marca> {
    let texto = std::fs::read_to_string(ruta_marca(app).ok()?).ok()?;
    serde_json::from_str(&texto).ok()
}

fn escribir_marca(app: &AppHandle, marca: &Marca) -> Result<(), String> {
    std::fs::create_dir_all(carpeta(app)?).map_err(|e| e.to_string())?;
    let texto = serde_json::to_string(marca).map_err(|e| e.to_string())?;
    std::fs::write(ruta_marca(app)?, texto).map_err(|e| e.to_string())
}

fn borrar_marca(app: &AppHandle) {
    if let Ok(r) = ruta_marca(app) {
        let _ = std::fs::remove_file(r);
    }
}

/// Un apunte en `actualizacion.log` (para saber qué pasó, y para las pruebas).
pub fn anotar(app: &AppHandle, texto: &str) {
    use std::io::Write;
    if let Ok(c) = carpeta(app) {
        let _ = std::fs::create_dir_all(&c);
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(c.join("actualizacion.log")) {
            let _ = writeln!(f, "{:.0} {texto}", crate::ahora());
        }
    }
}

/// De dónde se leen las actualizaciones. `MORGAN_ACTUALIZACIONES` lo cambia **solo para las
/// pruebas**: lo que venga de ahí tiene que estar firmado igual con la clave de Morgan.
fn endpoint() -> String {
    std::env::var("MORGAN_ACTUALIZACIONES")
        .ok()
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| format!("{VERSIONES}/latest/download/latest.json"))
}

/// El plazo de la versión nueva; `MORGAN_PLAZO_ESTRENO` lo acorta **solo para las pruebas**.
fn plazo() -> f64 {
    std::env::var("MORGAN_PLAZO_ESTRENO")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(bandeja::PLAZO_ESTRENO)
}

/// Comprueba una firma de Tauri (minisign, en base64) con la clave pública de Morgan.
pub fn comprobar(datos: &[u8], firma_b64: &str) -> Result<(), String> {
    let b64 = base64::engine::general_purpose::STANDARD;
    let clave = String::from_utf8(b64.decode(CLAVE_PUBLICA).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    let firma = String::from_utf8(b64.decode(firma_b64.trim()).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    let clave = minisign_verify::PublicKey::decode(&clave).map_err(|e| e.to_string())?;
    let firma = minisign_verify::Signature::decode(&firma).map_err(|e| e.to_string())?;
    clave.verify(datos, &firma, false).map_err(|e| e.to_string())
}

fn bajar(url: &str) -> Result<Vec<u8>, String> {
    let respuesta = ureq::get(url).timeout(Duration::from_secs(120)).call().map_err(|e| e.to_string())?;
    let mut datos = Vec::new();
    respuesta
        .into_reader()
        .take(300 * 1024 * 1024)
        .read_to_end(&mut datos)
        .map_err(|e| e.to_string())?;
    Ok(datos)
}

/// Baja el instalador de la versión que corre y lo guarda, con su firma comprobada.
fn preparar_anterior(app: &AppHandle, version: &str) -> Result<PathBuf, String> {
    let nombre = format!("Morgan.para.Windows_{version}_x64-setup.exe");
    let base = std::env::var("MORGAN_VERSIONES_ANTERIORES")
        .ok()
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| format!("{VERSIONES}/download/v{version}"));
    let datos = bajar(&format!("{base}/{nombre}"))?;
    let firma = String::from_utf8(bajar(&format!("{base}/{nombre}.sig"))?).map_err(|e| e.to_string())?;
    comprobar(&datos, &firma)?;
    let destino = carpeta(app)?.join(&nombre);
    std::fs::create_dir_all(carpeta(app)?).map_err(|e| e.to_string())?;
    std::fs::write(&destino, &datos).map_err(|e| e.to_string())?;
    std::fs::write(destino.with_extension("exe.sig"), firma).map_err(|e| e.to_string())?;
    Ok(destino)
}

/// Si hay una versión más nueva publicada (y firmada): su número.
pub async fn buscar(app: &AppHandle) -> Result<Option<tauri_plugin_updater::Update>, String> {
    let url = endpoint().parse::<tauri::Url>().map_err(|e| e.to_string())?;
    let actualizador = app
        .updater_builder()
        .endpoints(vec![url])
        .map_err(|e| e.to_string())?
        .build()
        .map_err(|e| e.to_string())?;
    actualizador.check().await.map_err(|e| e.to_string())
}

/// Instala la versión nueva (la persona ya dijo que sí). En Windows, el instalador cierra el
/// programa y lo vuelve a abrir en la versión nueva, que se juzga sola (`juzgar`).
pub async fn actualizar(app: &AppHandle, nueva: tauri_plugin_updater::Update) -> Result<(), String> {
    let actual = app.package_info().version.to_string();
    let antes_conectado = crate::leer_clave() == Clave::Conectado;
    let anterior = match preparar_anterior(app, &actual) {
        Ok(ruta) => Some(ruta.to_string_lossy().to_string()),
        Err(e) => {
            anotar(app, &format!("sin vuelta atrás a la {actual}: {e}"));
            None
        }
    };
    escribir_marca(app, &Marca {
        de: actual.clone(),
        a: nueva.version.clone(),
        desde: crate::ahora(),
        antes_conectado,
        anterior,
        volviendo: false,
    })?;
    anotar(app, &format!("actualizando de la {actual} a la {}", nueva.version));
    if let Err(e) = nueva.download_and_install(|_, _| {}, || {}).await {
        borrar_marca(app);
        anotar(app, &format!("no se pudo instalar la {}: {e}", nueva.version));
        return Err(e.to_string());
    }
    Ok(())
}

/// Lo que la bandeja tiene que decir de la última actualización, si algo.
pub enum Noticia {
    Nada,
    /// Se está juzgando la versión nueva (hilo lanzado).
    Juzgando,
    /// Se volvió a la anterior: «la X no conectó el PC».
    Volvio { a: String },
    /// La nueva no llegó a instalarse (sigue la de antes).
    NoSeInstalo { a: String },
}

/// Al arrancar: qué hacer con la marca de una actualización.
pub fn juzgar(app: &AppHandle, al_terminar: impl Fn(&AppHandle, Veredicto, &Marca) + Send + 'static) -> Noticia {
    let Some(marca) = leer_marca(app) else { return Noticia::Nada };
    let actual = app.package_info().version.to_string();
    if marca.volviendo {
        borrar_marca(app);
        if actual == marca.de {
            anotar(app, &format!("vuelta atrás hecha: de nuevo en la {actual}"));
            return Noticia::Volvio { a: marca.a };
        }
        anotar(app, &format!("la vuelta atrás a la {} no se instaló; sigue la {actual}", marca.de));
        return Noticia::Nada;
    }
    if actual != marca.a {
        // La actualización no llegó a instalarse: sigue la de antes.
        borrar_marca(app);
        anotar(app, &format!("la {} no se instaló; sigue la {actual}", marca.a));
        return Noticia::NoSeInstalo { a: marca.a };
    }
    let app = app.clone();
    std::thread::spawn(move || {
        let mut despierto = 0.0_f64;
        loop {
            let antes = Instant::now();
            std::thread::sleep(Duration::from_secs(5));
            let paso = antes.elapsed().as_secs_f64();
            // Si una vuelta duró mucho más de 5 s, el PC estuvo suspendido: no cuenta.
            if paso < 15.0 {
                despierto += paso;
            }
            let v = bandeja::veredicto(marca.antes_conectado, crate::leer_clave(), despierto, plazo());
            match v {
                Veredicto::Esperar => continue,
                Veredicto::Confirmar => {
                    borrar_marca(&app);
                    anotar(&app, &format!("la {} se confirma", marca.a));
                }
                Veredicto::VolverAtras => volver_atras(&app, &marca),
            }
            al_terminar(&app, v, &marca);
            return;
        }
    });
    Noticia::Juzgando
}

/// Reinstala la versión anterior, si se pudo guardar y su firma sigue valiendo.
fn volver_atras(app: &AppHandle, marca: &Marca) {
    let Some(ruta) = marca.anterior.clone() else {
        borrar_marca(app);
        anotar(app, &format!("la {} no conecta y no hay vuelta atrás", marca.a));
        return;
    };
    let ruta = PathBuf::from(ruta);
    let comprobada = std::fs::read(&ruta)
        .map_err(|e| e.to_string())
        .and_then(|datos| {
            let firma = std::fs::read_to_string(ruta.with_extension("exe.sig")).map_err(|e| e.to_string())?;
            comprobar(&datos, &firma)
        });
    if let Err(e) = comprobada {
        borrar_marca(app);
        anotar(app, &format!("la {} no conecta y la anterior no se puede comprobar: {e}", marca.a));
        return;
    }
    let mut volviendo = marca.clone();
    volviendo.volviendo = true;
    if escribir_marca(app, &volviendo).is_err() {
        return;
    }
    anotar(app, &format!("la {} no conectó el PC en {:.0} s: se vuelve a la {}", marca.a, plazo(), marca.de));
    // Sin ventanas (/P) y como actualización (/UPDATE): instala encima, sin desinstalar; y lo
    // vuelve a abrir en la bandeja (/R con /ARGS: sin /R, el instalador no lo abre). Él cierra
    // este.
    let _ = Command::new(&ruta).args(["/P", "/UPDATE", "/R", "/ARGS", "--bandeja"]).spawn();
}
