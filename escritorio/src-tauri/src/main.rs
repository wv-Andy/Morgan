// Morgan para Windows (5.0, decisión W1: Tauri). Una ventana para emparejar el PC sin abrir
// la consola, el instalador y, desde la 5.1, **el icono en la bandeja**: el estado (conectado,
// conectando, en pausa…), pausar y reanudar al momento, abrir Morgan y sus ajustes, lo último
// que hizo en el PC y el aviso de versión nueva. Desde la 5.2, **la conversación en su propia
// ventana** (la web de Morgan en WebView2) y el atajo Ctrl+Alt+M para abrirla desde cualquier
// sitio. El trabajo lo hace el agente de siempre, congelado con PyInstaller (decisión W2), que
// va dentro: `agente/morgan-agente/`.
// Ver docs/plan-5.0.md.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod bandeja;

use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::Command;
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use tauri::image::Image;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

use bandeja::{Clave, Lectura};

/// Sin ventana de consola al lanzar el agente (CREATE_NO_WINDOW).
const SIN_VENTANA: u32 = 0x0800_0000;
/// La web de Morgan, para «Abrir Morgan».
const WEB: &str = "https://morgan-ia.vercel.app";
/// Lo único que abre el aviso de versión nueva (el agente ya lo comprueba; aquí, otra vez).
const PAGINA_DE_VERSIONES: &str = "https://github.com/wv-Andy/Morgan/releases/";
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
    /// La página de la versión nueva, si la hay.
    pagina: Mutex<Option<String>>,
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
    let nueva = b.pagina.lock().unwrap().is_some();
    let _ = b.icono.set_tooltip(Some(if nueva {
        format!("Morgan — {texto} · Hay una versión nueva")
    } else {
        format!("Morgan — {texto}")
    }));
    let _ = b.estado.set_text(texto);
    let (pausar, activa) = bandeja::pausa(clave);
    let _ = b.pausa.set_text(if pausar { "Pausar Morgan en este PC" } else { "Reanudar Morgan" });
    let _ = b.pausa.set_enabled(activa);
    let _ = app.emit_to("main", "estado", texto);
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

fn mirar_novedades(app: &AppHandle) {
    let Ok(texto) = agente(app, &["novedades"]) else { return };
    let Ok(datos) = serde_json::from_str::<serde_json::Value>(texto.trim()) else { return };
    if datos.get("hay").and_then(|v| v.as_bool()) != Some(true) {
        return;
    }
    let (Some(version), Some(pagina)) = (
        datos.get("version").and_then(|v| v.as_str()),
        datos.get("pagina").and_then(|v| v.as_str()),
    ) else {
        return;
    };
    if !pagina.starts_with(PAGINA_DE_VERSIONES) {
        return;
    }
    let Some(b) = app.try_state::<Bandeja>() else { return };
    *b.pagina.lock().unwrap() = Some(pagina.to_string());
    let _ = b.novedad.set_text(format!("Descargar la versión nueva ({version})"));
    let _ = b.novedad.set_enabled(true);
    pintar(app, true);
}

// --- La conversación (5.2) ----------------------------------------------------------------

/// El atajo para abrir (o esconder) la conversación desde cualquier sitio (decidido el 2026-10-04).
const ATAJO: &str = "Ctrl+Alt+M";

fn atajo() -> Shortcut {
    Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::KeyM)
}

/// La ventana de la conversación: la web de Morgan, con la sesión guardada en el perfil de
/// WebView2 del programa (se entra una vez). **No tiene ninguna capacidad de Tauri**: los
/// permisos (`capabilities/default.json`) son solo de la ventana del programa, así que la web
/// no puede pausar, emparejar ni nada del PC. Solo navega por Morgan; lo demás, al navegador.
fn conversacion(app: &AppHandle) -> Result<(), String> {
    if let Some(ventana) = app.get_webview_window("morgan") {
        let _ = ventana.show();
        let _ = ventana.unminimize();
        let _ = ventana.set_focus();
        return Ok(());
    }
    let url = WEB.parse::<tauri::Url>().map_err(|e| format!("{e}"))?;
    let version = app.package_info().version.to_string();
    WebviewWindowBuilder::new(app, "morgan", WebviewUrl::External(url))
        .title("Morgan")
        .inner_size(1100.0, 780.0)
        .min_inner_size(420.0, 520.0)
        // Que la web sepa que está dentro del programa (no ofrece descargarlo, por ejemplo).
        .initialization_script(&format!("window.__MORGAN_PROGRAMA__ = {version:?};"))
        // Soltar archivos en la conversación: si Tauri se queda el arrastre, la web no lo ve.
        .disable_drag_drop_handler()
        .on_navigation(|url| {
            let queda = bandeja::se_queda_en_la_ventana(url.scheme(), url.host_str());
            if !queda {
                let _ = abrir_enlace(url.as_str());
            }
            queda
        })
        .build()
        .map(|_| ())
        .map_err(|e| e.to_string())
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

/// El atajo: si la conversación está delante, se esconde; si no, se enseña.
fn alternar_conversacion(app: &AppHandle) {
    if let Some(ventana) = app.get_webview_window("morgan") {
        if ventana.is_visible().unwrap_or(false) && delante(&ventana) {
            let _ = ventana.hide();
            return;
        }
    }
    let _ = conversacion(app);
}

fn mostrar(app: &AppHandle, seccion: Option<&str>) {
    if let Some(ventana) = app.get_webview_window("main") {
        let _ = ventana.show();
        let _ = ventana.unminimize();
        let _ = ventana.set_focus();
        if let Some(s) = seccion {
            let _ = app.emit_to("main", "ir", s);
        }
    }
}

fn crear_bandeja(app: &AppHandle) -> tauri::Result<()> {
    let estado = MenuItem::with_id(app, "estado", "Comprobando…", false, None::<&str>)?;
    let pausa = MenuItem::with_id(app, "pausa", "Pausar Morgan en este PC", false, None::<&str>)?;
    let web = MenuItem::with_id(app, "web", format!("Abrir Morgan ({ATAJO})"), true, None::<&str>)?;
    let ajustes = MenuItem::with_id(app, "ajustes", "Qué puede hacer y qué carpetas ve", true, None::<&str>)?;
    let ultimas = MenuItem::with_id(app, "ultimas", "Lo último que hizo en este PC", true, None::<&str>)?;
    let ventana = MenuItem::with_id(app, "ventana", "Este PC: estado y emparejar", true, None::<&str>)?;
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
            &ventana,
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
            "web" => {
                let _ = conversacion(app);
            }
            "ajustes" => {
                let _ = abrir_ajustes(app.clone());
            }
            "ultimas" => mostrar(app, Some("ultimas")),
            "ventana" => mostrar(app, None),
            "novedad" => {
                if let Some(b) = app.try_state::<Bandeja>() {
                    if let Some(p) = b.pagina.lock().unwrap().clone() {
                        let _ = abrir_enlace(&p);
                    }
                }
            }
            "salir" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|icono, evento| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } =
                evento
            {
                // Un clic: la conversación, que es para lo que se abre Morgan.
                let _ = conversacion(icono.app_handle());
            }
        })
        .build(app)?;
    app.manage(Bandeja {
        icono,
        estado,
        pausa,
        novedad,
        ultima: Mutex::new(None),
        pagina: Mutex::new(None),
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

// --- Las órdenes de la ventana -----------------------------------------------------------

#[tauri::command]
fn estado(app: AppHandle) -> Result<String, String> {
    agente(&app, &["estado"])
}

/// El estado como lo pinta la bandeja: [clave, texto]. Sin lanzar el agente.
#[tauri::command]
fn resumen() -> (String, String) {
    let clave = leer_clave();
    (format!("{clave:?}"), bandeja::texto(clave).to_string())
}

#[tauri::command]
fn pausar(app: AppHandle) -> Result<String, String> {
    let r = agente(&app, &["pausar"]);
    pintar(&app, true);
    r
}

#[tauri::command]
fn reanudar(app: AppHandle) -> Result<String, String> {
    let r = agente(&app, &["reanudar"]);
    pintar(&app, true);
    r
}

/// Lo último que pidió la nube en este PC (JSON del agente: cuándo, qué, detalle y estado).
#[tauri::command]
fn ultimas(app: AppHandle) -> Result<String, String> {
    agente(&app, &["ultimas", "--cuantas", "20"])
}

fn codigo_valido(codigo: &str) -> Result<String, String> {
    let codigo = codigo.trim().to_string();
    if codigo.is_empty() || codigo.len() > 64 || !codigo.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return Err("Escribe el código tal cual te lo da la web (Ajustes → Tu equipo).".into());
    }
    Ok(codigo)
}

/// De qué cuenta es el código, sin usarlo: la ventana lo enseña y pregunta antes de emparejar
/// (alguien podría darte un código de SU cuenta). Medido en la primera prueba de punta a punta:
/// el agente lo preguntaba en la consola, y la ventana no tiene, así que no emparejaba nunca.
#[tauri::command]
fn consultar(app: AppHandle, codigo: String) -> Result<String, String> {
    let codigo = codigo_valido(&codigo)?;
    let texto = agente(&app, &["emparejar", "--codigo", &codigo, "--consultar"])?;
    texto
        .lines()
        .find_map(|l| l.strip_prefix("CUENTA: "))
        .map(|c| c.trim().to_string())
        .ok_or_else(|| texto.trim().to_string())
}

/// Empareja (la persona ya confirmó la cuenta en la ventana) y deja el agente arrancando con
/// Windows.
#[tauri::command]
fn emparejar(app: AppHandle, codigo: String) -> Result<String, String> {
    let codigo = codigo_valido(&codigo)?;
    let mut texto = agente(&app, &["emparejar", "--codigo", &codigo, "--si"])?;
    texto.push_str(&agente(&app, &["arranque", "activar"])?);
    pintar(&app, true);
    Ok(texto)
}

/// «Morgan en tu PC»: las carpetas y lo que puede hacer. Sin consola.
#[tauri::command]
fn abrir_ajustes(app: AppHandle) -> Result<(), String> {
    let exe = carpeta_del_agente(&app)?.join("morgan-agente-fondo.exe");
    Command::new(exe)
        .arg("ajustes")
        .creation_flags(SIN_VENTANA)
        .spawn()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

/// «Abrir Morgan» en la ventana del programa: la conversación, en su ventana (5.2).
#[tauri::command]
fn abrir_web(app: AppHandle) -> Result<(), String> {
    conversacion(&app)
}

fn main() {
    tauri::Builder::default()
        // Una sola: abrir el programa con la bandeja ya en marcha enseña su ventana.
        .plugin(tauri_plugin_single_instance::init(|app, _argumentos, _carpeta| {
            mostrar(app, None);
        }))
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, atajo_pulsado, evento| {
                    if atajo_pulsado == &atajo() && evento.state() == ShortcutState::Pressed {
                        alternar_conversacion(app);
                    }
                })
                .build(),
        )
        .setup(|app| {
            crear_bandeja(app.handle())?;
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
            abrir_ajustes,
            abrir_web
        ])
        .run(tauri::generate_context!())
        .expect("Morgan no pudo arrancar");
}
