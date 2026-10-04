// El estado del icono de la bandeja (5.1), a partir de lo que el agente ya deja en su carpeta:
// si está emparejado (`agente.json` y `credencial.bin`), la marca de pausa (`pausa.json`) y su
// salud (`salud.json`: el estado del canal y un pulso cada 5 s, 3.6). Se lee cada pocos
// segundos sin lanzar ningún proceso.
//
// Sin dependencias a propósito: así se prueba (y se muta) con `rustc --test` en segundos, sin
// compilar Tauri. Ver .github/workflows/escritorio.yml y escritorio/mutaciones.ps1.

/// Cuánto vale un pulso: el agente late cada 5 s; sin pulso en 20 s, no está conectado.
pub const PULSO_VIGENTE: f64 = 20.0;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Clave {
    SinEmparejar,
    EnPausa,
    Conectado,
    Conectando,
    Desconectado,
    Revocado,
    Rendido,
}

/// Lo que se sabe del agente, leído de su carpeta.
pub struct Lectura<'a> {
    pub emparejado: bool,
    pub en_pausa: bool,
    pub estado: Option<&'a str>,
    pub pulso: Option<f64>,
    pub arrancado: Option<f64>,
}

fn reciente(momento: Option<f64>, ahora: f64) -> bool {
    matches!(momento, Some(m) if ahora - m <= PULSO_VIGENTE)
}

pub fn resumen(l: &Lectura, ahora: f64) -> Clave {
    if !l.emparejado {
        return Clave::SinEmparejar;
    }
    if l.en_pausa {
        return Clave::EnPausa;
    }
    match l.estado {
        Some("REVOKED") | Some("INCOMPATIBLE") | Some("OUTDATED") => return Clave::Revocado,
        Some("RENDIDO") => return Clave::Rendido,
        _ => {}
    }
    // Recién lanzado aún no ha latido: cuenta su arranque.
    if !reciente(l.pulso, ahora) && !reciente(l.arrancado, ahora) {
        return Clave::Desconectado;
    }
    // Conectado es READY, como en la web: la nube lo tiene, no solo un socket abierto.
    if l.estado == Some("READY") && reciente(l.pulso, ahora) {
        Clave::Conectado
    } else {
        Clave::Conectando
    }
}

pub fn texto(c: Clave) -> &'static str {
    match c {
        Clave::SinEmparejar => "Sin emparejar: abre la ventana para conectar este PC",
        Clave::EnPausa => "En pausa: Morgan no puede hacer nada en este PC",
        Clave::Conectado => "Conectado: Morgan puede usar este PC",
        Clave::Conectando => "Conectando…",
        Clave::Desconectado => "Desconectado: el agente no está en marcha",
        Clave::Revocado => "Desemparejado en la nube: vuelve a emparejar este PC",
        Clave::Rendido => "Parado tras fallar varias veces seguidas",
    }
}

/// El icono de color solo cuando está conectado; el gris, en todo lo demás.
pub fn de_color(c: Clave) -> bool {
    c == Clave::Conectado
}

/// Si el menú ofrece pausar (y no reanudar), y si ofrece alguna de las dos.
pub fn pausa(c: Clave) -> (bool, bool) {
    match c {
        Clave::SinEmparejar => (true, false),
        Clave::EnPausa => (false, true),
        _ => (true, true),
    }
}

// --- La vuelta atrás de una actualización (5.3) --------------------------------------------

/// Lo que tiene una versión recién instalada para volver a conectar el PC antes de volver a la
/// anterior: 3 minutos **despierto** (decisión de 2026-10-04). Como el agente desde la 3.8.5.
pub const PLAZO_ESTRENO: f64 = 180.0;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Veredicto {
    /// Aún dentro del plazo: se sigue mirando.
    Esperar,
    /// La versión nueva vale: se borra la marca y se dice.
    Confirmar,
    /// No volvió a conectar en el plazo: se reinstala la anterior.
    VolverAtras,
}

/// Qué hacer con una versión recién instalada. `antes_conectado`: si el PC estaba conectado al
/// pulsar «Actualizar» (si no, no hay con qué comparar: se confirma, y no se castiga a una
/// versión buena por una red caída). `despierto`: los segundos que lleva el programa mirando
/// sin contar una suspensión del PC.
pub fn veredicto(antes_conectado: bool, ahora: Clave, despierto: f64, plazo: f64) -> Veredicto {
    if !antes_conectado {
        return Veredicto::Confirmar;
    }
    match ahora {
        Clave::Conectado => Veredicto::Confirmar,
        // Pausarlo es cosa de la persona, no un fallo de la versión.
        Clave::EnPausa => Veredicto::Confirmar,
        _ if despierto >= plazo => Veredicto::VolverAtras,
        _ => Veredicto::Esperar,
    }
}

/// La web de Morgan, la única página de fuera que se abre en la ventana del programa.
pub const ANFITRION_DE_MORGAN: &str = "morgan-ia.vercel.app";

/// Qué hacer cuando la ventana va a cargar una dirección (5.3: una sola ventana).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Navegacion {
    /// Se carga en la ventana: la web de Morgan y la pantalla de emparejar del programa.
    Queda,
    /// Se abre en el navegador: un enlace de una respuesta, GitHub, la ayuda… La ventana no es
    /// un navegador, y así una página ajena nunca se carga con la sesión de Morgan al lado.
    AlNavegador,
    /// Ni una cosa ni otra (`data:`, `javascript:`, `file:`…): no hay nada que abrir.
    Nada,
}

/// La regla de la ventana. La pantalla del programa se sirve en `http://tauri.localhost` en
/// Windows (`tauri://localhost` en los demás). `about:blank` es lo primero que carga WebView2:
/// bloquearlo dejaba la ventana en negro (la conversación de la 5.2, en mi captura).
pub fn navegacion(esquema: &str, anfitrion: Option<&str>) -> Navegacion {
    match (esquema, anfitrion) {
        ("https", Some(ANFITRION_DE_MORGAN)) => Navegacion::Queda,
        ("http", Some("tauri.localhost")) | ("tauri", Some("localhost")) => Navegacion::Queda,
        ("about", None) => Navegacion::Queda,
        ("http" | "https" | "mailto", _) => Navegacion::AlNavegador,
        _ => Navegacion::Nada,
    }
}

#[cfg(test)]
mod pruebas {
    use super::*;

    #[test]
    fn conectada_la_version_nueva_se_confirma() {
        assert_eq!(veredicto(true, Clave::Conectado, 10.0, PLAZO_ESTRENO), Veredicto::Confirmar);
    }

    #[test]
    fn sin_conectar_se_espera_y_pasado_el_plazo_se_vuelve() {
        for c in [Clave::Desconectado, Clave::Conectando, Clave::Revocado, Clave::Rendido, Clave::SinEmparejar] {
            assert_eq!(veredicto(true, c, 30.0, PLAZO_ESTRENO), Veredicto::Esperar, "{c:?}");
            assert_eq!(veredicto(true, c, PLAZO_ESTRENO, PLAZO_ESTRENO), Veredicto::VolverAtras, "{c:?}");
        }
    }

    #[test]
    fn si_antes_no_estaba_conectado_no_se_castiga_a_la_nueva() {
        assert_eq!(veredicto(false, Clave::Desconectado, 999.0, PLAZO_ESTRENO), Veredicto::Confirmar);
    }

    #[test]
    fn en_pausa_no_es_un_fallo() {
        assert_eq!(veredicto(true, Clave::EnPausa, 999.0, PLAZO_ESTRENO), Veredicto::Confirmar);
    }

    #[test]
    fn el_plazo_son_tres_minutos() {
        assert_eq!(PLAZO_ESTRENO, 180.0);
        assert_eq!(veredicto(true, Clave::Desconectado, 179.0, PLAZO_ESTRENO), Veredicto::Esperar);
    }

    #[test]
    fn en_la_ventana_solo_morgan_y_la_pantalla_del_programa() {
        use Navegacion::*;
        assert_eq!(navegacion("https", Some("morgan-ia.vercel.app")), Queda);
        assert_eq!(navegacion("http", Some("tauri.localhost")), Queda);
        assert_eq!(navegacion("tauri", Some("localhost")), Queda);
        assert_eq!(navegacion("about", None), Queda);
        assert_eq!(navegacion("http", Some("morgan-ia.vercel.app")), AlNavegador);
        assert_eq!(navegacion("https", Some("github.com")), AlNavegador);
        assert_eq!(navegacion("https", Some("morgan-ia.vercel.app.otro.com")), AlNavegador);
        assert_eq!(navegacion("https", Some("otro-morgan-ia.vercel.app")), AlNavegador);
        assert_eq!(navegacion("https", Some("tauri.localhost")), AlNavegador);
        assert_eq!(navegacion("http", Some("localhost")), AlNavegador);
        assert_eq!(navegacion("mailto", None), AlNavegador);
        assert_eq!(navegacion("file", None), Nada);
        assert_eq!(navegacion("data", None), Nada);
        assert_eq!(navegacion("javascript", None), Nada);
        assert_eq!(navegacion("about", Some("x")), Nada);
    }

    const AHORA: f64 = 1_000_000.0;

    fn lectura(estado: Option<&'static str>, pulso: Option<f64>) -> Lectura<'static> {
        Lectura { emparejado: true, en_pausa: false, estado, pulso, arrancado: None }
    }

    #[test]
    fn conectado_con_ready_y_pulso_reciente() {
        assert_eq!(resumen(&lectura(Some("READY"), Some(AHORA - 4.0)), AHORA), Clave::Conectado);
    }

    #[test]
    fn un_pulso_viejo_es_desconectado_aunque_dijera_ready() {
        // El agente murió con READY apuntado: el fichero se queda, el pulso envejece.
        assert_eq!(resumen(&lectura(Some("READY"), Some(AHORA - 60.0)), AHORA), Clave::Desconectado);
        assert_eq!(resumen(&lectura(Some("READY"), Some(AHORA - PULSO_VIGENTE - 0.5)), AHORA), Clave::Desconectado);
        assert_eq!(resumen(&lectura(None, None), AHORA), Clave::Desconectado);
    }

    #[test]
    fn reconectando_con_pulso_es_conectando() {
        assert_eq!(resumen(&lectura(Some("RECONNECTING"), Some(AHORA - 3.0)), AHORA), Clave::Conectando);
    }

    #[test]
    fn recien_arrancado_sin_pulso_es_conectando() {
        let l = Lectura { emparejado: true, en_pausa: false, estado: Some("CONNECTING"), pulso: None,
                          arrancado: Some(AHORA - 2.0) };
        assert_eq!(resumen(&l, AHORA), Clave::Conectando);
        // Y aunque diga READY, sin pulso propio no se da por conectado.
        let l = Lectura { estado: Some("READY"), ..l };
        assert_eq!(resumen(&l, AHORA), Clave::Conectando);
    }

    #[test]
    fn la_pausa_manda_sobre_la_salud() {
        let l = Lectura { emparejado: true, en_pausa: true, estado: Some("READY"), pulso: Some(AHORA),
                          arrancado: None };
        assert_eq!(resumen(&l, AHORA), Clave::EnPausa);
    }

    #[test]
    fn sin_emparejar_manda_sobre_todo() {
        let l = Lectura { emparejado: false, en_pausa: true, estado: Some("READY"), pulso: Some(AHORA),
                          arrancado: None };
        assert_eq!(resumen(&l, AHORA), Clave::SinEmparejar);
    }

    #[test]
    fn revocado_y_rendido_aunque_el_pulso_sea_viejo() {
        assert_eq!(resumen(&lectura(Some("REVOKED"), Some(AHORA - 999.0)), AHORA), Clave::Revocado);
        assert_eq!(resumen(&lectura(Some("INCOMPATIBLE"), None), AHORA), Clave::Revocado);
        assert_eq!(resumen(&lectura(Some("RENDIDO"), Some(AHORA - 999.0)), AHORA), Clave::Rendido);
    }

    #[test]
    fn el_color_solo_conectado() {
        assert!(de_color(Clave::Conectado));
        for c in [Clave::SinEmparejar, Clave::EnPausa, Clave::Conectando, Clave::Desconectado,
                  Clave::Revocado, Clave::Rendido] {
            assert!(!de_color(c), "{c:?}");
        }
    }

    #[test]
    fn pausar_o_reanudar() {
        assert_eq!(pausa(Clave::EnPausa), (false, true));
        assert_eq!(pausa(Clave::Conectado), (true, true));
        assert_eq!(pausa(Clave::Desconectado), (true, true));
        assert_eq!(pausa(Clave::SinEmparejar), (true, false));
    }
}
