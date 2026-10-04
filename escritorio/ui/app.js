// La ventana de Morgan para Windows (5.0): emparejar sin consola y abrir los ajustes.
// Las órdenes van al agente congelado a través de Tauri (src-tauri/src/main.rs).
const { invoke } = window.__TAURI__.core;

const $ = (id) => document.getElementById(id);

function ver(seccion) {
  for (const id of ["cargando", "sin-emparejar", "emparejado"]) {
    $(id).hidden = id !== seccion;
  }
}

/** Sin credencial que valga: hay que emparejar (otra vez, si la revocaron). */
const SIN_EMPAREJAR = ["UNPAIRED", "PAIRING", "REVOKED"];

/** Lo que dice `estado`: su primera línea es «Estado: <VALOR>» (src/agente/estado.py). */
function leerEstado(texto) {
  const valor = (/^Estado: ([A-Z_]+)/m.exec(texto) || [])[1] || "UNPAIRED";
  return { emparejado: !SIN_EMPAREJAR.includes(valor), valor, texto: texto.trim() };
}

async function mirar() {
  ver("cargando");
  try {
    const { emparejado, texto } = leerEstado(await invoke("estado"));
    if (emparejado) {
      $("detalle").textContent = texto;
      ver("emparejado");
    } else {
      ver("sin-emparejar");
      $("codigo").focus();
    }
  } catch (error) {
    ver("sin-emparejar");
  }
}

$("formulario").addEventListener("submit", async (evento) => {
  evento.preventDefault();
  const boton = $("emparejar");
  $("error").hidden = true;
  boton.disabled = true;
  boton.textContent = "Conectando…";
  try {
    await invoke("emparejar", { codigo: $("codigo").value });
    await mirar();
  } catch (error) {
    $("error").textContent = String(error).trim() || "No se pudo conectar. Mira que el código sea el de ahora.";
    $("error").hidden = false;
  } finally {
    boton.disabled = false;
    boton.textContent = "Conectar";
  }
});

$("ajustes").addEventListener("click", () => invoke("abrir_ajustes"));
$("web").addEventListener("click", () => invoke("abrir_web"));
$("enlace-web").addEventListener("click", (evento) => {
  evento.preventDefault();
  invoke("abrir_web");
});

mirar();
