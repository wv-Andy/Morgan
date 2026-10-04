// Morgan para Windows (5.0, decisión W1: Tauri). Una ventana para emparejar el PC sin abrir
// la consola, el instalador y, desde la 5.1, **el icono en la bandeja**: el estado (conectado,
// conectando, en pausa…), pausar y reanudar al momento, abrir Morgan y sus ajustes, lo último
// que hizo en el PC y el aviso de versión nueva. Desde la 5.2, **la conversación en su propia
// ventana** (la web de Morgan en WebView2) y el atajo Ctrl+Alt+M para abrirla desde cualquier
// sitio. Desde la 5.3, **una sola ventana**: la web de Morgan tal cual, con lo del programa en
// Ajustes → Este PC (la web solo puede pedirle lo inofensivo), y las actualizaciones firmadas.
// El trabajo lo hace el agente de siempre, congelado con PyInstaller (decisión W2), que va
// dentro: `agente/morgan-agente/`.
// Ver docs/plan-5.0.md.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod actualizar;
mod bandeja;

use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::Command;
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use tauri::image::Image;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent};
use tauri::webview::PageLoadEvent;
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

use bandeja::{Clave, Lectura};

/// Sin ventana de consola al lanzar el agente (CREATE_NO_WINDOW).
const SIN_VENTANA: u32 = 0x0800_0000;
/// La web de Morgan: lo que enseña la ventana.
const WEB: &str = "https://morgan-ia.vercel.app";
/// Cada cuánto se mira el estado del agente (leer tres ficheros pequeños).
const CADA: Duration = Duration::from_secs(3);
/// Cuándo se mira si hay versión nueva: al minuto de arrancar y después una vez al día.
const PRIMERA_NOVEDAD: Duration = Duration::from_secs(60);
const CADA_NOVEDAD: Duration = Duration::from_secs(24 * 3600);

const ICONO_CONECTADO: &[u8] = include_bytes!("../bandeja/conectado.png");
const ICONO_APAGADO: &[u8] = include_bytes!("../bandeja/apagado.png");

fn carpeta_del_agente(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .resource_dir()
        .map(|d| d.join("agente").join("morgan-agente"))
        .map_err(|e| format!("No se encuentra el agente: {e}"))
}

/// Una orden al agente (el ejecutable con consola, para leer lo que dice), sin ventana.
fn agente(app: &AppHandle, argumentos: &[&str]) -> Result<String, String> {
    let exe = carpeta_del_agente(app)?.join("morgan-agente.exe");
    let salida = Command::new(&exe)
        .args(argumentos)
        .env("PYTHONIOENCODING", "utf-8")
        .creation_flags(SIN_VENTANA)
        .output()
        .map_err(|e| format!("No se pudo arrancar el agente ({}): {e}", exe.display()))?;
    let texto = format!(
        "{}{}",
        String::from_utf8_lossy(&salida.stdout),
        String::from_utf8_lossy(&salida.stderr)
    );
    if salida.status.success() {
        Ok(texto)
    } else {
        Err(texto)
    }
}

fn abrir_enlace(url: &str) -> Result<(), String> {
    Command::new("rundll32")
        .args(["url.dll,FileProtocolHandler", url])
        .creation_flags(SIN_VENTANA)
        .spawn()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

// --- El estado, leído de la carpeta del agente ---------------------------------------------

/// La carpeta del estado del agente: la misma que usa él (`src/agente/estado.py`).
fn estado_del_agente() -> PathBuf {
    if let Ok(propia) = std::env::var("MORGAN_AGENTE_DIR") {
        if !propia.is_empty() {
            return PathBuf::from(propia);
        }
    }
    let base = std::env::var("LOCALAPPDATA").unwrap_or_default();
    PathBuf::from(base).join("Morgan").join("agente")
}

fn ahora() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

fn leer_clave() -> Clave {
    let carpeta = estado_del_agente();
    let salud: serde_json::Value = std::fs::read_to_string(carpeta.join("salud.json"))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or(serde_json::Value::Null);
    let lectura = Lectura {
        emparejado: carpeta.join("agente.json").exists() && carpeta.join("credencial.bin").exists(),
        en_pausa: carpeta.join("pausa.json").exists(),
        estado: salud.get("estado").and_then(|v| v.as_str()),
        pulso: salud.get("pulso").and_then(|v| v.as_f64()),
        arrancado: salud.get("arrancado").and_then(|v| v.as_f64()),
    };
    bandeja::resumen(&lectura, ahora())
}

// --- La bandeja --------------------------------------------------------------------------

struct Bandeja {
    icono: TrayIcon<tauri::Wry>,
    estado: MenuItem<tauri::Wry>,
    pausa: MenuItem<tauri::Wry>,
    novedad: MenuItem<tauri::Wry>,
    /// Lo último que se pintó, para no tocar el icono si nada cambió.
    ultima: Mutex<Option<Clave>>,
    /// La versión nueva, si la hay (5.3: se instala al pulsar).
    nueva: Mutex<Option<String>>,
}

fn pintar(app: &AppHandle, forzar: bool) {
    let Some(b) = app.try_state::<Bandeja>() else { return };
    let clave = leer_clave();
    {
        let mut ultima = b.ultima.lock().unwrap();
        if !forzar && *ultima == Some(clave) {
            return;
        }
        *ultima = Some(clave);
    }
    let icono = if bandeja::de_color(clave) { ICONO_CONECTADO } else { ICONO_APAGADO };
    if let Ok(imagen) = Image::from_bytes(icono) {
        let _ = b.icono.set_icon(Some(imagen));
    }
    let texto = bandeja::texto(clave);
    let nueva = b.nueva.lock().unwrap().is_some();
    let _ = b.icono.set_tooltip(Some(if nueva {
        format!("Morgan — {texto} · Hay una versión nueva")
    } else {
        format!("Morgan — {texto}")
    }));
    let _ = b.estado.set_text(texto);
    let (pausar, activa) = bandeja::pausa(clave);
    let _ = b.pausa.set_text(if pausar { "Pausar Morgan en este PC" } else { "Reanudar Morgan" });
    let _ = b.pausa.set_enabled(activa);
}

/// Pausar o reanudar, según esté. En un hilo: pausar puede tardar hasta ~2 s.
fn alternar_pausa(app: &AppHandle) {
    let app = app.clone();
    std::thread::spawn(move || {
        let orden = if leer_clave() == Clave::EnPausa { "reanudar" } else { "pausar" };
        let _ = agente(&app, &[orden]);
        pintar(&app, true);
    });
}

/// Si hay versión nueva publicada y firmada (5.3: el actualizador de Tauri, `latest.json`).
fn mirar_novedades(app: &AppHandle) {
    let Ok(Some(nueva)) = tauri::async_runtime::block_on(actualizar::buscar(app)) else { return };
    let Some(b) = app.try_state::<Bandeja>() else { return };
    *b.nueva.lock().unwrap() = Some(nueva.version.clone());
    let _ = b.novedad.set_text(format!("Actualizar a la versión {}", nueva.version));
    let _ = b.novedad.set_enabled(true);
    pintar(app, true);
}

/// «Actualizar ahora»: la persona ya dijo que sí al pulsar (decisión del 2026-10-04). El
/// instalador cierra el programa y lo abre en la versión nueva, que se juzga sola.
fn instalar_novedad(app: &AppHandle) {
    let app = app.clone();
    std::thread::spawn(move || {
        if let Some(b) = app.try_state::<Bandeja>() {
            let _ = b.novedad.set_text("Actualizando…");
            let _ = b.novedad.set_enabled(false);
        }
        let hecho = tauri::async_runtime::block_on(async {
            match actualizar::buscar(&app).await {
                Ok(Some(nueva)) => actualizar::actualizar(&app, nueva).await,
                Ok(None) => Err("ya está al día".to_string()),
                Err(e) => Err(e),
            }
        });
        if let Err(e) = hecho {
            actualizar::anotar(&app, &format!("no se pudo actualizar: {e}"));
            if let Some(b) = app.try_state::<Bandeja>() {
                let _ = b.novedad.set_text("No se pudo actualizar (se probará mañana)");
            }
        }
    });
}

/// Lo que se dice en la bandeja tras juzgar una actualización.
fn contar_actualizacion(app: &AppHandle, texto: String) {
    if let Some(b) = app.try_state::<Bandeja>() {
        let _ = b.novedad.set_text(&texto);
        let _ = b.novedad.set_enabled(false);
        let _ = b.icono.set_tooltip(Some(format!("Morgan — {texto}")));
    }
}

// --- La ventana (5.3: una sola) ----------------------------------------------------------

/// El atajo para abrir (o esconder) Morgan desde cualquier sitio (decidido el 2026-10-04).
const ATAJO: &str = "Ctrl+Alt+M";
/// La pantalla del propio programa (emparejar), servida por Tauri. En Windows, en
/// `http://tauri.localhost`.
const PANTALLA: &str = "index.html";
const PANTALLA_URL: &str = "http://tauri.localhost/index.html";

fn atajo() -> Shortcut {
    Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::KeyM)
}

fn emparejado() -> bool {
    let carpeta = estado_del_agente();
    carpeta.join("agente.json").exists() && carpeta.join("credencial.bin").exists()
}

/// A qué parte de la web llevar cuando acabe de cargar (la ventana recién creada aún no la tiene).
struct Pendiente(Mutex<Option<String>>);

/// Le dice a la página que abra una parte suya («este-pc»: Ajustes → Este PC). Se deja también
/// en `window.__MORGAN_IR__`, por si la web aún no escuchaba (la lee al arrancar).
fn ir(ventana: &tauri::WebviewWindow, parte: &str) {
    let parte = serde_json::to_string(parte).unwrap_or_default();
    let _ = ventana.eval(&format!(
        "window.__MORGAN_IR__={parte};window.dispatchEvent(new CustomEvent('morgan-programa',{{detail:{{ir:{parte}}}}}))"
    ));
}

/// La ventana de Morgan: **la web, tal cual** (con la sesión guardada en el perfil de WebView2
/// del programa) si el PC está emparejado, y si no, la pantalla del programa para emparejarlo;
/// al acabar, la misma ventana pasa a la web. Lo pedí el 2026-10-04: «no me gusta que haya
/// dos ventanas». Se crea la primera vez que hace falta, no al iniciar sesión (WebView2 son
/// ~100 MB), y siempre desde el bucle de la aplicación: creada desde una orden síncrona, se
/// queda colgada en Windows.
///
/// Lo que la web puede pedirle al programa lo dicen `capabilities/web.json` (solo lo inofensivo)
/// y la regla de `bandeja::navegacion` (solo Morgan y la pantalla del programa se cargan aquí).
fn ventana(app: &AppHandle) -> Option<tauri::WebviewWindow> {
    if let Some(v) = app.get_webview_window("main") {
        return Some(v);
    }
    let url = if emparejado() {
        WebviewUrl::External(WEB.parse().ok()?)
    } else {
        WebviewUrl::App(PANTALLA.into())
    };
    let version = app.package_info().version.to_string();
    let constructor = WebviewWindowBuilder::new(app, "main", url);
    // Solo en la copia de prueba (la característica `depurar`): la prueba mira la página por
    // dentro. WebView2 ignora su variable de entorno porque Tauri le pasa sus propios argumentos.
    #[cfg(feature = "depurar")]
    let constructor = match std::env::var("MORGAN_DEPURAR_PUERTO") {
        Ok(puerto) => constructor.additional_browser_args(&format!(
            "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection --remote-debugging-port={puerto}"
        )),
        Err(_) => constructor,
    };
    constructor
        .title("Morgan")
        .inner_size(1100.0, 780.0)
        .min_inner_size(420.0, 520.0)
        .visible(false)
        // Que la web sepa que está dentro del programa (enseña Ajustes → Este PC, y no ofrece
        // descargarlo).
        .initialization_script(&format!("window.__MORGAN_PROGRAMA__ = {version:?};"))
        // Soltar archivos en la conversación: si Tauri se queda el arrastre, la web no lo ve.
        .disable_drag_drop_handler()
        .on_navigation(|url| match bandeja::navegacion(url.scheme(), url.host_str()) {
            bandeja::Navegacion::Queda => true,
            bandeja::Navegacion::AlNavegador => {
                let _ = abrir_enlace(url.as_str());
                false
            }
            bandeja::Navegacion::Nada => false,
        })
        .on_page_load(|ventana, carga| {
            if !matches!(carga.event(), PageLoadEvent::Finished) {
                return;
            }
            let pendiente = ventana.app_handle().try_state::<Pendiente>().and_then(|p| p.0.lock().unwrap().take());
            if let Some(parte) = pendiente {
                ir(&ventana, &parte);
            }
        })
        .build()
        .map_err(|e| eprintln!("No se pudo abrir la ventana de Morgan: {e}"))
        .ok()
}

#[link(name = "user32")]
extern "system" {
    fn GetForegroundWindow() -> isize;
}

/// Si la ventana está en primer plano. `is_focused()` dice que no cuando el foco está dentro de
/// la web (lo tiene el control de WebView2, no la ventana): medido en GitHub, con la
/// conversación delante y el foco en ella, el atajo no la escondía.
fn delante(ventana: &tauri::WebviewWindow) -> bool {
    let Ok(h) = ventana.hwnd() else { return false };
    let primer_plano = unsafe { GetForegroundWindow() };
    primer_plano == h.0 as isize
}

/// El atajo: si Morgan está delante, se esconde; si no, se enseña.
fn alternar(app: &AppHandle) {
    if let Some(v) = app.get_webview_window("main") {
        if v.is_visible().unwrap_or(false) && delante(&v) {
            let _ = v.hide();
            return;
        }
    }
    mostrar(app, None);
}

/// Enseña la ventana (creándola si hace falta) y, si se pide, una parte de la web.
fn mostrar(app: &AppHandle, parte: Option<&str>) {
    let nueva = app.get_webview_window("main").is_none();
    if nueva {
        if let (Some(p), Some(pendiente)) = (parte, app.try_state::<Pendiente>()) {
            *pendiente.0.lock().unwrap() = Some(p.to_string());
        }
    }
    let Some(v) = ventana(app) else { return };
    let _ = v.show();
    let _ = v.unminimize();
    let _ = v.set_focus();
    if let (Some(p), false) = (parte, nueva) {
        ir(&v, p);
    }
}

fn navegar(app: &AppHandle, destino: &str) -> Result<(), String> {
    let v = app.get_webview_window("main").ok_or("No está la ventana de Morgan")?;
    v.navigate(destino.parse().map_err(|e| format!("{e}"))?).map_err(|e| e.to_string())
}

fn crear_bandeja(app: &AppHandle) -> tauri::Result<()> {
    let estado = MenuItem::with_id(app, "estado", "Comprobando…", false, None::<&str>)?;
    let pausa = MenuItem::with_id(app, "pausa", "Pausar Morgan en este PC", false, None::<&str>)?;
    let web = MenuItem::with_id(app, "web", format!("Abrir Morgan ({ATAJO})"), true, None::<&str>)?;
    let ajustes = MenuItem::with_id(app, "ajustes", "Qué puede hacer y qué carpetas ve", true, None::<&str>)?;
    let ultimas = MenuItem::with_id(app, "ultimas", "Lo último que hizo en este PC", true, None::<&str>)?;
    let novedad = MenuItem::with_id(app, "novedad", "Al día", false, None::<&str>)?;
    let salir = MenuItem::with_id(app, "salir", "Quitar de la bandeja (Morgan sigue)", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &estado,
            &PredefinedMenuItem::separator(app)?,
            &pausa,
            &web,
            &ajustes,
            &ultimas,
            &PredefinedMenuItem::separator(app)?,
            &novedad,
            &salir,
        ],
    )?;
    let icono = TrayIconBuilder::with_id("morgan")
        .icon(Image::from_bytes(ICONO_APAGADO)?)
        .tooltip("Morgan")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, evento| match evento.id().as_ref() {
            "pausa" => alternar_pausa(app),
            "web" => mostrar(app, None),
            "ajustes" => {
                let _ = abrir_ajustes_del_agente(app);
            }
            "ultimas" => mostrar(app, Some("este-pc")),
            "novedad" => instalar_novedad(app),
            "salir" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|icono, evento| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } =
                evento
            {
                // Un clic: Morgan.
                mostrar(icono.app_handle(), None);
            }
        })
        .build(app)?;
    app.manage(Bandeja {
        icono,
        estado,
        pausa,
        novedad,
        ultima: Mutex::new(None),
        nueva: Mutex::new(None),
    });
    pintar(app, true);

    let mirar = app.clone();
    std::thread::spawn(move || loop {
        std::thread::sleep(CADA);
        pintar(&mirar, false);
    });
    let novedades = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(PRIMERA_NOVEDAD);
        loop {
            mirar_novedades(&novedades);
            std::thread::sleep(CADA_NOVEDAD);
        }
    });
    Ok(())
}

// --- Las órdenes ------------------------------------------------------------------------
//
// Quién puede pedir cada una lo dicen `capabilities/`: la pantalla del programa, todas; la web
// de Morgan (`capabilities/web.json`), solo las inofensivas: el estado, pausar y reanudar, lo
// último que hizo, abrir «Qué puede hacer y qué carpetas ve», ir a la pantalla de emparejar y
// la versión. **Emparejar no**: una web comprometida no puede conectar el PC a otra cuenta.
// Las que lanzan el agente son `async`: las síncronas corren en el hilo de la ventana, y
// pausar (hasta ~2 s) la dejaba congelada.

/// Lo que dice el agente de sí mismo (`morgan-agente estado`). Solo la pantalla del programa.
#[tauri::command]
async fn estado(app: AppHandle) -> Result<String, String> {
    agente(&app, &["estado"])
}

#[derive(serde::Serialize)]
struct Resumen {
    clave: String,
    texto: &'static str,
    /// Si el botón dice «Pausar» (si no, «Reanudar»), y si se puede pulsar.
    pausar: bool,
    se_puede: bool,
}

/// El estado como lo pinta la bandeja, sin lanzar el agente.
#[tauri::command]
fn resumen() -> Resumen {
    let clave = leer_clave();
    let (pausar, se_puede) = bandeja::pausa(clave);
    Resumen { clave: format!("{clave:?}"), texto: bandeja::texto(clave), pausar, se_puede }
}

#[tauri::command]
async fn pausar(app: AppHandle) -> Result<String, String> {
    let r = agente(&app, &["pausar"]);
    pintar(&app, true);
    r
}

#[tauri::command]
async fn reanudar(app: AppHandle) -> Result<String, String> {
    let r = agente(&app, &["reanudar"]);
    pintar(&app, true);
    r
}

/// Lo último que pidió la nube en este PC (JSON del agente: cuándo, qué, detalle y estado).
#[tauri::command]
async fn ultimas(app: AppHandle) -> Result<String, String> {
    agente(&app, &["ultimas", "--cuantas", "20"])
}

fn codigo_valido(codigo: &str) -> Result<String, String> {
    let codigo = codigo.trim().to_string();
    if codigo.is_empty() || codigo.len() > 64 || !codigo.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return Err("Escribe el código tal cual te lo da la web (Ajustes → Tu equipo).".into());
    }
    Ok(codigo)
}

/// De qué cuenta es el código, sin usarlo: la pantalla lo enseña y pregunta antes de emparejar
/// (alguien podría darte un código de SU cuenta). Solo la pantalla del programa.
#[tauri::command]
async fn consultar(app: AppHandle, codigo: String) -> Result<String, String> {
    let codigo = codigo_valido(&codigo)?;
    let texto = agente(&app, &["emparejar", "--codigo", &codigo, "--consultar"])?;
    texto
        .lines()
        .find_map(|l| l.strip_prefix("CUENTA: "))
        .map(|c| c.trim().to_string())
        .ok_or_else(|| texto.trim().to_string())
}

/// Empareja (la persona ya confirmó la cuenta en la pantalla) y deja el agente arrancando con
/// Windows. Solo la pantalla del programa.
#[tauri::command]
async fn emparejar(app: AppHandle, codigo: String) -> Result<String, String> {
    let codigo = codigo_valido(&codigo)?;
    let mut texto = agente(&app, &["emparejar", "--codigo", &codigo, "--si"])?;
    texto.push_str(&agente(&app, &["arranque", "activar"])?);
    pintar(&app, true);
    Ok(texto)
}

/// Desconecta este PC de la cuenta (la revoca en la nube y borra la credencial), para poder
/// conectarlo con otra, o otra vez si lo revocaron: un PC revocado guarda la credencial, y
/// emparejar decía «ya está emparejado». Solo la pantalla del programa, y tras preguntar.
#[tauri::command]
async fn desemparejar(app: AppHandle) -> Result<String, String> {
    let r = agente(&app, &["desemparejar"]);
    pintar(&app, true);
    r
}

/// «Qué puede hacer y qué carpetas ve»: la ventana de ajustes del agente. Sin consola.
fn abrir_ajustes_del_agente(app: &AppHandle) -> Result<(), String> {
    let exe = carpeta_del_agente(app)?.join("morgan-agente-fondo.exe");
    Command::new(exe)
        .arg("ajustes")
        .creation_flags(SIN_VENTANA)
        .spawn()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn abrir_ajustes(app: AppHandle) -> Result<(), String> {
    abrir_ajustes_del_agente(&app)
}

/// De la pantalla del programa a la web (al acabar de emparejar, o «Volver a Morgan»).
#[tauri::command]
fn abrir_web(app: AppHandle) -> Result<(), String> {
    navegar(&app, WEB)
}

/// De la web a la pantalla del programa, para emparejar con otra cuenta. Solo navega: el código
/// se escribe allí, y allí se pregunta de quién es antes de usarlo.
#[tauri::command]
fn pantalla_emparejar(app: AppHandle) -> Result<(), String> {
    navegar(&app, PANTALLA_URL)
}

#[derive(serde::Serialize)]
struct Programa {
    version: String,
    /// La versión nueva publicada, si la hay (se mira al minuto de arrancar y una vez al día).
    nueva: Option<String>,
}

#[tauri::command]
fn programa(app: AppHandle) -> Programa {
    let nueva = app.try_state::<Bandeja>().and_then(|b| b.nueva.lock().unwrap().clone());
    Programa { version: app.package_info().version.to_string(), nueva }
}

/// «Actualizar ahora» desde Ajustes → Este PC: lo mismo que en la bandeja. Solo instala la
/// versión publicada **y firmada** con la clave del programa; ninguna otra.
#[tauri::command]
fn actualizar_ahora(app: AppHandle) {
    instalar_novedad(&app);
}

fn main() {
    tauri::Builder::default()
        // Una sola: abrir el programa con la bandeja ya en marcha enseña Morgan.
        .plugin(tauri_plugin_single_instance::init(|app, _argumentos, _carpeta| {
            mostrar(app, None);
        }))
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, atajo_pulsado, evento| {
                    if atajo_pulsado == &atajo() && evento.state() == ShortcutState::Pressed {
                        alternar(app);
                    }
                })
                .build(),
        )
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            app.manage(Pendiente(Mutex::new(None)));
            crear_bandeja(app.handle())?;
            // Una actualización recién instalada se juzga: si no vuelve a conectar el PC en 3
            // minutos despierto, se vuelve a la anterior (5.3).
            let version = app.package_info().version.to_string();
            let noticia = actualizar::juzgar(app.handle(), move |app, veredicto, marca| {
                let texto = match veredicto {
                    bandeja::Veredicto::Confirmar => format!("Actualizado a la {}", marca.a),
                    _ => format!("La {} no conectó el PC: volviendo a la {}…", marca.a, marca.de),
                };
                contar_actualizacion(app, texto);
            });
            let recien = !matches!(noticia, actualizar::Noticia::Nada);
            match noticia {
                actualizar::Noticia::Volvio { a } => {
                    contar_actualizacion(app.handle(), format!("Volví a la {version}: la {a} no conectó el PC"))
                }
                actualizar::Noticia::NoSeInstalo { a } => {
                    contar_actualizacion(app.handle(), format!("No se pudo instalar la {a}: sigue la {version}"))
                }
                actualizar::Noticia::Juzgando | actualizar::Noticia::Nada => {}
            }
            // Solo para las pruebas: buscar e instalar sin el menú (quien lo lanza ya dijo que sí).
            // El instalador vuelve a abrir el programa con los mismos argumentos: recién
            // actualizado (o no), no se repite, o la prueba daría vueltas sin fin.
            if !recien && std::env::args().any(|a| a == "--actualizar") {
                let app = app.handle().clone();
                std::thread::spawn(move || {
                    let hecho = tauri::async_runtime::block_on(async {
                        match actualizar::buscar(&app).await {
                            Ok(Some(nueva)) => actualizar::actualizar(&app, nueva).await,
                            Ok(None) => Err("al día".to_string()),
                            Err(e) => Err(e),
                        }
                    });
                    if let Err(e) = hecho {
                        actualizar::anotar(&app, &format!("--actualizar: {e}"));
                    }
                });
            }
            // Si otro programa ya tiene el atajo, Morgan sigue sin él (desde la bandeja).
            if let Err(e) = app.global_shortcut().register(atajo()) {
                eprintln!("No se pudo reservar {ATAJO}: {e}");
            }
            // Al iniciar sesión (la clave Run del instalador) arranca solo en la bandeja.
            if !std::env::args().any(|a| a == "--bandeja") {
                mostrar(app.handle(), None);
            }
            Ok(())
        })
        // Cerrar la ventana la esconde: el programa sigue en la bandeja.
        .on_window_event(|ventana, evento| {
            if let WindowEvent::CloseRequested { api, .. } = evento {
                api.prevent_close();
                let _ = ventana.hide();
            }
        })
        .invoke_handler(tauri::generate_handler![
            estado,
            resumen,
            pausar,
            reanudar,
            ultimas,
            consultar,
            emparejar,
            desemparejar,
            abrir_ajustes,
            abrir_web,
            pantalla_emparejar,
            programa,
            actualizar_ahora
        ])
        .run(tauri::generate_context!())
        .expect("Morgan no pudo arrancar");
}
