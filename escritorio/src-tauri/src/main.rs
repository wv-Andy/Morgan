// Morgan para Windows (5.0, decisión W1: Tauri). Una ventana para emparejar el PC sin abrir
// la consola, y el instalador. El trabajo lo hace el agente de siempre, congelado con
// PyInstaller (decisión W2), que va dentro: `agente/morgan-agente/morgan-agente.exe`.
// Ver docs/plan-5.0.md.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::Command;

use tauri::Manager;

/// Sin ventana de consola al lanzar el agente (CREATE_NO_WINDOW).
const SIN_VENTANA: u32 = 0x0800_0000;
/// La web de Morgan, para «Abrir Morgan».
const WEB: &str = "https://morgan-ia.vercel.app";

fn carpeta_del_agente(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    app.path()
        .resource_dir()
        .map(|d| d.join("agente").join("morgan-agente"))
        .map_err(|e| format!("No se encuentra el agente: {e}"))
}

/// Una orden al agente (el ejecutable con consola, para leer lo que dice), sin ventana.
fn agente(app: &tauri::AppHandle, argumentos: &[&str]) -> Result<String, String> {
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

#[tauri::command]
fn estado(app: tauri::AppHandle) -> Result<String, String> {
    agente(&app, &["estado"])
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
fn consultar(app: tauri::AppHandle, codigo: String) -> Result<String, String> {
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
fn emparejar(app: tauri::AppHandle, codigo: String) -> Result<String, String> {
    let codigo = codigo_valido(&codigo)?;
    let mut texto = agente(&app, &["emparejar", "--codigo", &codigo, "--si"])?;
    texto.push_str(&agente(&app, &["arranque", "activar"])?);
    Ok(texto)
}

/// «Morgan en tu PC»: las carpetas y lo que puede hacer. Sin consola.
#[tauri::command]
fn abrir_ajustes(app: tauri::AppHandle) -> Result<(), String> {
    let exe = carpeta_del_agente(&app)?.join("morgan-agente-fondo.exe");
    Command::new(exe)
        .arg("ajustes")
        .creation_flags(SIN_VENTANA)
        .spawn()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn abrir_web() -> Result<(), String> {
    Command::new("rundll32")
        .args(["url.dll,FileProtocolHandler", WEB])
        .creation_flags(SIN_VENTANA)
        .spawn()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![estado, consultar, emparejar, abrir_ajustes, abrir_web])
        .run(tauri::generate_context!())
        .expect("Morgan no pudo arrancar");
}
