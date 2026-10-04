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

/// La web de Morgan, la única que se abre dentro de la ventana de la conversación (5.2).
pub const ANFITRION_DE_MORGAN: &str = "morgan-ia.vercel.app";

/// Si una dirección se queda en la ventana de Morgan. Todo lo demás (un enlace de una
/// respuesta, GitHub, la ayuda) se abre en el navegador: la ventana no es un navegador, y
/// así una página ajena nunca se carga con la sesión de Morgan al lado.
pub fn se_queda_en_la_ventana(esquema: &str, anfitrion: Option<&str>) -> bool {
    esquema == "https" && anfitrion == Some(ANFITRION_DE_MORGAN)
}

#[cfg(test)]
mod pruebas {
    use super::*;

    #[test]
    fn solo_morgan_por_https_se_queda_en_la_ventana() {
        assert!(se_queda_en_la_ventana("https", Some("morgan-ia.vercel.app")));
        assert!(!se_queda_en_la_ventana("http", Some("morgan-ia.vercel.app")));
        assert!(!se_queda_en_la_ventana("https", Some("github.com")));
        assert!(!se_queda_en_la_ventana("https", Some("morgan-ia.vercel.app.otro.com")));
        assert!(!se_queda_en_la_ventana("https", Some("otro-morgan-ia.vercel.app")));
        assert!(!se_queda_en_la_ventana("https", None));
        assert!(!se_queda_en_la_ventana("file", None));
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
