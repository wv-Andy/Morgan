// La ventana de Morgan para Windows (5.0): emparejar sin consola y abrir los ajustes. Desde la
// 5.1, también pausar y reanudar y lo último que hizo (lo mismo que ofrece la bandeja).
// Las órdenes van al agente congelado a través de Tauri (src-tauri/src/main.rs).
const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

const $ = (id) => document.getElementById(id);

function ver(seccion) {
  for (const id of ["cargando", "sin-emparejar", "confirmar", "emparejado", "ultimas"]) {
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
      await pintarEstado();
      ver("emparejado");
    } else {
      ver("sin-emparejar");
      $("codigo").focus();
    }
  } catch (error) {
    ver("sin-emparejar");
  }
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
    await mirar();
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

/** El estado como lo pinta la bandeja (sin lanzar el agente): [clave, texto]. */
async function pintarEstado() {
  const [clave, texto] = await invoke("resumen");
  $("titulo-estado").textContent = texto;
  $("pausa").textContent = clave === "EnPausa" ? "Reanudar Morgan" : "Pausar Morgan en este PC";
  $("pausa").dataset.clave = clave;
}

$("pausa").addEventListener("click", async () => {
  const boton = $("pausa");
  const orden = boton.dataset.clave === "EnPausa" ? "reanudar" : "pausar";
  boton.disabled = true;
  boton.textContent = orden === "pausar" ? "Pausando…" : "Reanudando…";
  try {
    await invoke(orden);
  } finally {
    boton.disabled = false;
    await pintarEstado();
  }
});

function hora(segundos) {
  return new Date(segundos * 1000).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" });
}

/** Lo último que pidió Morgan a este PC (el diario del agente, 24 h). */
async function verUltimas() {
  ver("ultimas");
  const lista = $("lista");
  lista.replaceChildren();
  let ultimas = [];
  try {
    ultimas = JSON.parse(await invoke("ultimas"));
  } catch (error) {
    ultimas = [];
  }
  for (const u of ultimas) {
    const li = document.createElement("li");
    const cuando = document.createElement("span");
    cuando.className = "cuando";
    cuando.textContent = hora(u.cuando);
    const estado = document.createElement("span");
    estado.className = "estado";
    estado.textContent = u.estado;
    li.append(estado, cuando, document.createTextNode(u.que));
    if (u.detalle) {
      const detalle = document.createElement("span");
      detalle.className = "detalle";
      detalle.textContent = u.detalle;
      li.append(detalle);
    }
    lista.append(li);
  }
  $("sin-ultimas").hidden = ultimas.length > 0;
}

$("ver-ultimas").addEventListener("click", verUltimas);
$("volver").addEventListener("click", () => mirar());

// La bandeja avisa cuando cambia el estado, y pide abrir una sección («Lo último que hizo»).
listen("estado", () => {
  if (!$("emparejado").hidden) pintarEstado();
});
listen("ir", (evento) => {
  if (evento.payload === "ultimas") verUltimas();
});

$("ajustes").addEventListener("click", () => invoke("abrir_ajustes"));
$("web").addEventListener("click", () => invoke("abrir_web"));
$("enlace-web").addEventListener("click", (evento) => {
  evento.preventDefault();
  invoke("abrir_web");
});

mirar();
