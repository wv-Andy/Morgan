// La pantalla del propio programa (5.0; desde la 5.3, solo para emparejar). Sale en la ventana
// de Morgan cuando el PC aún no está conectado, y al acabar la misma ventana pasa a la web,
// donde está lo demás (Ajustes → Este PC). Emparejar va aquí y no en la web: es lo único que da
// acceso al PC, y la web no puede pedirlo (src-tauri/capabilities/web.json).
// Las órdenes van al agente congelado a través de Tauri (src-tauri/src/main.rs).
const { invoke } = window.__TAURI__.core;

const $ = (id) => document.getElementById(id);

function ver(seccion) {
  for (const id of ["cargando", "conectado", "sin-emparejar", "confirmar"]) {
    $(id).hidden = id !== seccion;
  }
}

/** Sin credencial: hay que emparejar. Revocado guarda la credencial: sale «conectado», con
 * «Desconectar» para volver a empezar. */
const SIN_EMPAREJAR = ["UNPAIRED", "PAIRING"];

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
      ver("conectado");
      $("volver").focus();
      return;
    }
  } catch (error) {
    // Sin estado legible: se ofrece emparejar, que es lo que arregla casi todo.
  }
  ver("sin-emparejar");
  $("codigo").focus();
}

// 1. De qué cuenta es el código (sin usarlo). 2. La persona dice que sí. 3. Emparejar.
$("formulario").addEventListener("submit", async (evento) => {
  evento.preventDefault();
  const boton = $("emparejar");
  $("error").hidden = true;
  boton.disabled = true;
  boton.textContent = "Mirando…";
  try {
    $("cuenta").textContent = await invoke("consultar", { codigo: $("codigo").value });
    $("error2").hidden = true;
    ver("confirmar");
    $("si").focus();
  } catch (error) {
    $("error").textContent = String(error).trim() || "No se pudo comprobar el código. Mira que sea el de ahora.";
    $("error").hidden = false;
  } finally {
    boton.disabled = false;
    boton.textContent = "Conectar";
  }
});

$("si").addEventListener("click", async () => {
  const boton = $("si");
  boton.disabled = true;
  boton.textContent = "Conectando…";
  try {
    await invoke("emparejar", { codigo: $("codigo").value });
    // Conectado: la misma ventana pasa a la web de Morgan.
    await invoke("abrir_web");
  } catch (error) {
    $("error2").textContent = String(error).trim() || "No se pudo conectar.";
    $("error2").hidden = false;
  } finally {
    boton.disabled = false;
    boton.textContent = "Sí, es mía: conectar";
  }
});

$("no").addEventListener("click", () => {
  $("codigo").value = "";
  ver("sin-emparejar");
  $("codigo").focus();
});

$("desconectar").addEventListener("click", async () => {
  const boton = $("desconectar");
  if (boton.dataset.seguro !== "1") {
    // Dos pulsaciones: la primera solo pregunta.
    boton.dataset.seguro = "1";
    boton.textContent = "¿Seguro? Pulsa otra vez para desconectarlo";
    return;
  }
  boton.disabled = true;
  boton.textContent = "Desconectando…";
  $("error3").hidden = true;
  try {
    await invoke("desemparejar");
    await mirar();
  } catch (error) {
    $("error3").textContent = String(error).trim() || "No se pudo desconectar.";
    $("error3").hidden = false;
  } finally {
    boton.disabled = false;
    boton.dataset.seguro = "";
    boton.textContent = "Desconectar este PC";
  }
});

$("volver").addEventListener("click", () => invoke("abrir_web"));
$("enlace-web").addEventListener("click", (evento) => {
  evento.preventDefault();
  invoke("abrir_web");
});

mirar();
