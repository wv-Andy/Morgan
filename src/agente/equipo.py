"""
El PC por dentro, de lectura (4.7, mi lista: información del sistema, red,
servicios). Todo **sin administrador**: el agente corre como la persona y nunca se eleva.

- `resumen()` completa `system_info`, que siempre responde: la edición de Windows, el
  procesador, los discos y su espacio, la GPU, la batería y cuánto lleva encendido. Es
  del equipo, no de la persona: ni etiquetas de los discos, ni usuario, ni entorno (§19).
- `pc_diagnostics(aspecto)` es una capacidad aparte, **apagada al nacer** (la red y los
  puertos dicen más de lo que hay en el PC): `rendimiento` («¿por qué va lento?»), `red`
  (interfaces e IP, sin las direcciones físicas), `puertos` (qué escucha y qué programa) y
  `servicios` (su estado; los automáticos que no están en marcha, para diagnosticar).

Las temperaturas no se dan: en Windows solo salen de WMI con administrador (medido en el
mi PC: `psutil` no las tiene), y se dice así en vez de inventarlas.
"""

import platform
import sys
import time

from src.agente import control, motor

#: Lo que se dice de las temperaturas, que Windows no da sin administrador.
SIN_TEMPERATURAS = "Windows no las da sin permisos de administrador, y Morgan no se eleva."
#: Cuánto se mide la CPU para «rendimiento»: por debajo, el primer dato es ruido.
MUESTRA_CPU = 0.5
MAX_PROCESOS = 8
MAX_SERVICIOS = 40
_TIPOS_DE_DISCO = {2: "extraíble", 3: "fijo", 4: "de red", 5: "CD/DVD", 6: "RAM"}


def _ok(datos) -> dict:
    return {"success": True, "data": datos, "error": None}


def _no(motivo: str, mensaje: str) -> dict:
    return {"success": False, "data": None, "error": mensaje, "motivo": motivo}


def legible(bytes_: float) -> str:
    from src.agente.archivos import legible as _legible

    return _legible(int(bytes_))


def _registro(ruta: str, *nombres):
    """Valores de HKLM, o None si no están (o no es Windows)."""
    if sys.platform != "win32":
        return [None] * len(nombres)
    import winreg

    try:
        clave = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ruta)
    except OSError:
        return [None] * len(nombres)
    valores = []
    for nombre in nombres:
        try:
            valores.append(winreg.QueryValueEx(clave, nombre)[0])
        except OSError:
            valores.append(None)
    return valores


def windows() -> str:
    """«Windows 11 Pro 25H2 (compilación 26200)». El registro dice «Windows 10» también en
    Windows 11 (medido en mi PC, compilación 26200): se mira la compilación."""
    producto, version, compilacion = _registro(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
                                               "ProductName", "DisplayVersion", "CurrentBuild")
    if not producto:
        return f"{platform.system()} {platform.release()}"
    try:
        if int(compilacion) >= 22000:
            producto = producto.replace("Windows 10", "Windows 11")
    except (TypeError, ValueError):
        pass
    return " ".join(p for p in (producto, version, f"(compilación {compilacion})" if compilacion else "") if p)


def procesador() -> str | None:
    (nombre,) = _registro(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString")
    return nombre.strip() if isinstance(nombre, str) else platform.processor() or None


def graficas() -> list[dict]:
    """Las tarjetas gráficas, del registro de Windows (sin administrador ni WMI)."""
    if sys.platform != "win32":
        return []
    import winreg

    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        clase = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return []
    vistas, salida, i = set(), [], 0
    while True:
        try:
            sub = winreg.EnumKey(clase, i)
        except OSError:
            break
        i += 1
        nombre, memoria = _registro(f"{base}\\{sub}", "DriverDesc", "HardwareInformation.qwMemorySize")
        if not isinstance(nombre, str) or nombre in vistas:
            continue
        vistas.add(nombre)
        salida.append({"nombre": nombre,
                       "memoria": legible(memoria) if isinstance(memoria, int) and memoria > 0
                       else "compartida con la RAM (o no la declara)"})
    return salida


def _tipo_de_disco(raiz: str) -> str | None:
    if sys.platform != "win32":
        return None
    import ctypes

    return _TIPOS_DE_DISCO.get(ctypes.windll.kernel32.GetDriveTypeW(raiz))


def discos() -> list[dict]:
    import psutil

    salida = []
    for particion in psutil.disk_partitions(all=False):
        try:
            uso = psutil.disk_usage(particion.mountpoint)
        except OSError:
            continue            # un lector sin disco, o una unidad que no responde
        salida.append({"unidad": particion.mountpoint, "tipo": _tipo_de_disco(particion.mountpoint),
                       "sistema_de_archivos": particion.fstype, "total": legible(uso.total),
                       "libre": legible(uso.free), "usado_pct": round(uso.percent)})
    return salida


def bateria() -> dict | None:
    import psutil

    b = psutil.sensors_battery()
    if b is None:
        return None
    datos = {"porcentaje": round(b.percent), "enchufado": bool(b.power_plugged)}
    if not b.power_plugged and isinstance(b.secsleft, int) and b.secsleft > 0:
        datos["queda"] = f"{b.secsleft // 3600} h {b.secsleft % 3600 // 60} min"
    return datos


def resumen() -> dict:
    """Lo que `system_info` añade desde la 4.7. Cada parte, por su cuenta: una que falla
    no se lleva a las demás."""
    import psutil

    datos: dict = {}
    for clave, calcular in (("windows", windows), ("procesador", procesador), ("discos", discos),
                            ("graficas", graficas), ("bateria", bateria)):
        try:
            valor = calcular()
        except Exception:
            continue
        if valor not in (None, [], ""):
            datos[clave] = valor
    try:
        datos["nucleos_fisicos"] = psutil.cpu_count(logical=False)
        horas = (time.time() - psutil.boot_time()) / 3600
        datos["encendido_desde_hace"] = f"{int(horas // 24)} d {int(horas % 24)} h" if horas >= 24 else f"{horas:.1f} h".replace(".", ",")
    except Exception:
        pass
    datos["temperaturas"] = SIN_TEMPERATURAS
    return datos


# --- pc_diagnostics ---

def _rendimiento() -> dict:
    import psutil

    procesos = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            p.cpu_percent(None)             # la primera lectura prepara la medida
            procesos.append(p)
        except psutil.Error:
            continue
    total = psutil.cpu_percent(interval=MUESTRA_CPU)
    medidos = []
    nucleos = psutil.cpu_count() or 1
    for p in procesos:
        try:
            memoria = p.memory_info().rss
            cpu = p.cpu_percent(None) / nucleos
        except psutil.Error:
            continue
        if p.info["pid"] == 0:              # «System Idle Process» no es carga
            continue
        medidos.append({"nombre": p.info["name"], "pid": p.info["pid"],
                        "cpu_pct": round(cpu, 1), "memoria": legible(memoria), "_m": memoria})
    memoria = psutil.virtual_memory()
    intercambio = psutil.swap_memory()
    por_cpu = sorted(medidos, key=lambda d: d["cpu_pct"], reverse=True)[:MAX_PROCESOS]
    por_memoria = sorted(medidos, key=lambda d: d["_m"], reverse=True)[:MAX_PROCESOS]
    for d in medidos:
        d.pop("_m", None)
    datos = {
        "cpu_pct": round(total), "memoria_usada_pct": round(memoria.percent),
        "memoria_libre": legible(memoria.available), "memoria_total": legible(memoria.total),
        "intercambio_usado_pct": round(intercambio.percent),
        "mas_cpu": por_cpu, "mas_memoria": por_memoria,
        "discos": [{"unidad": d["unidad"], "libre": d["libre"], "usado_pct": d["usado_pct"]} for d in discos()],
    }
    pistas = []
    if total >= 85:
        pistas.append("La CPU está casi al máximo.")
    if memoria.percent >= 85:
        pistas.append("La memoria está casi llena: el PC usa el disco como memoria y va lento.")
    if any(d["usado_pct"] >= 90 for d in datos["discos"]):
        pistas.append("Algún disco está casi lleno.")
    if pistas:
        datos["pistas"] = pistas
    return datos


def _red() -> dict:
    import socket

    import psutil

    estados = psutil.net_if_stats()
    interfaces = []
    for nombre, direcciones in psutil.net_if_addrs().items():
        estado = estados.get(nombre)
        ips = [d.address for d in direcciones if d.family in (socket.AF_INET, socket.AF_INET6)]
        interfaces.append({"nombre": nombre, "conectada": bool(estado and estado.isup),
                           "velocidad_mbps": estado.speed if estado and estado.speed else None,
                           "ips": ips})
    interfaces.sort(key=lambda i: (not i["conectada"], i["nombre"]))
    return {"interfaces": interfaces,
            "nota": "Sin direcciones físicas (MAC). Para la puerta de enlace y el DNS: ipconfig /all (run_command)."}


def _puertos(filtro: str = "") -> dict:
    import psutil

    nombres: dict[int, str] = {}
    escuchando = []
    for c in psutil.net_connections(kind="inet"):
        es_tcp = c.type == 1          # SOCK_STREAM
        if es_tcp and c.status != psutil.CONN_LISTEN:
            continue
        if not es_tcp and c.raddr:
            continue
        if c.pid and c.pid not in nombres:
            try:
                nombres[c.pid] = psutil.Process(c.pid).name()
            except psutil.Error:
                nombres[c.pid] = "?"
        ip = c.laddr.ip if c.laddr else ""
        escuchando.append({"puerto": c.laddr.port if c.laddr else None, "protocolo": "TCP" if es_tcp else "UDP",
                           "abierto_a": "solo este PC" if ip in ("127.0.0.1", "::1") else "la red",
                           "programa": nombres.get(c.pid), "pid": c.pid})
    unicos = {(e["puerto"], e["protocolo"], e["pid"]): e for e in escuchando}
    todos = sorted(unicos.values(), key=lambda e: (e["protocolo"], e["puerto"] or 0))
    udp = sum(1 for e in todos if e["protocolo"] == "UDP")
    if filtro:
        f = str(filtro).lower()
        lista = [e for e in todos if f in str(e["puerto"]) or f in str(e["programa"]).lower()]
    else:
        # Sin filtro, solo TCP: medido en mi PC, con UDP eran ~4.000 caracteres
        # (~1.000 tokens) en cada llamada del turno, casi todo ruido del sistema.
        lista = [e for e in todos if e["protocolo"] == "TCP"]
    datos = {"escuchando": lista[:60], "total": len(lista), "is_truncated": len(lista) > 60}
    if not filtro and udp:
        datos["udp"] = f"{udp} puertos UDP abiertos; con filtro (un puerto o un programa) salen también."
    return datos


def _servicios(filtro: str = "") -> dict:
    import psutil

    if not hasattr(psutil, "win_service_iter"):
        return {"aviso": "Los servicios son de Windows."}
    todos = []
    for s in psutil.win_service_iter():
        try:
            todos.append({"nombre": s.name(), "visible": s.display_name(),
                          "estado": s.status(), "inicio": s.start_type()})
        except (psutil.Error, OSError):
            continue            # alguno no deja leer su configuración: se sigue
    en_marcha = sum(1 for s in todos if s["estado"] == "running")
    datos = {"total": len(todos), "en_marcha": en_marcha}
    if filtro:
        f = str(filtro).lower()
        elegidos = [s for s in todos if f in s["nombre"].lower() or f in s["visible"].lower()]
        datos["coinciden"] = elegidos[:MAX_SERVICIOS]
        datos["is_truncated"] = len(elegidos) > MAX_SERVICIOS
    else:
        datos["automaticos_parados"] = [s for s in todos if s["inicio"] == "automatic"
                                        and s["estado"] != "running"][:MAX_SERVICIOS]
        datos["nota"] = ("Un servicio automático parado puede ser normal (algunos arrancan solo "
                         "cuando hacen falta). Con filtro, se buscan por nombre.")
    return datos


ASPECTOS = {"rendimiento": _rendimiento, "red": _red, "puertos": _puertos, "servicios": _servicios}


def pc_diagnostics(aspecto: str = "", filtro: str = "", **_) -> dict:
    """Uno de `ASPECTOS`, de lectura. `filtro`, para puertos y servicios."""
    decision = motor.evaluar("diagnostico")
    if not decision:
        return _no(decision.motivo, decision.mensaje)
    calcular = ASPECTOS.get(str(aspecto or "").lower())
    if calcular is None:
        return _no("argumentos", f"aspecto tiene que ser uno de: {', '.join(ASPECTOS)}.")
    control.punto_seguro()
    try:
        datos = calcular(filtro) if aspecto in ("puertos", "servicios") else calcular()
    except Exception as exc:
        return _no("no_disponible", f"No se pudo leer: {exc}")
    return _ok(datos)


DIAGNOSTICO = {"pc_diagnostics": pc_diagnostics}


# --- Arrancar y parar un servicio (4.9) ---

#: Los que no se paran nunca desde Morgan: sin ellos Windows, la red o la protección caen.
SERVICIOS_PROTEGIDOS = frozenset(s.lower() for s in (
    "RpcSs", "RpcEptMapper", "DcomLaunch", "EventLog", "Winmgmt", "WinDefend", "MDCoreSvc", "mpssvc",
    "BFE", "Dhcp", "Dnscache", "nsi", "LanmanWorkstation", "Power", "ProfSvc", "Schedule", "SamSs",
    "LSM", "CryptSvc", "SecurityHealthService", "wscsvc", "WdNisSvc", "Sense", "gpsvc", "UserManager",
    "CoreMessagingRegistrar", "BrokerInfrastructure", "SystemEventsBroker", "TermService", "Themes",
    "AudioSrv", "AudioEndpointBuilder", "PlugPlay", "WlanSvc", "netprofm", "NlaSvc",
))
_ESTADOS_DE = {"iniciar": "running", "parar": "stopped"}


def service_control(nombre: str = "", accion: str = "", **_) -> dict:
    """Arranca, para o reinicia un servicio de Windows, con plan y «Permitir». Sin
    administrador (Morgan nunca se eleva): muchos servicios lo exigen, y entonces se dice."""
    import ctypes

    import psutil
    from src.agente.escritura import _autorizar

    accion = str(accion or "").lower()
    if accion not in ("iniciar", "parar", "reiniciar"):
        return _no("argumentos", "accion tiene que ser iniciar, parar o reiniciar.")
    try:
        servicio = psutil.win_service_get(str(nombre))
        real, visible, estado = servicio.name(), servicio.display_name(), servicio.status()
    except (psutil.Error, AttributeError, OSError):
        return _no("no_existe", f"No hay ningún servicio llamado «{nombre}» (se busca por su nombre corto).")
    if real.lower() in SERVICIOS_PROTEGIDOS and accion != "iniciar":
        return _no("protegido", f"«{visible}» es de los que sostienen Windows, la red o la protección: no se para.")

    reales, auditoria = _autorizar("service_control", f"{accion.upper()} el servicio «{visible}»",
                                   [(None, {})], detalle=f"{visible} ({real}) · ahora: {estado}")
    if reales is None:
        return auditoria
    control.sin_vuelta()
    advapi = ctypes.windll.advapi32
    advapi.OpenSCManagerW.restype = advapi.OpenServiceW.restype = ctypes.c_void_p
    gestor = advapi.OpenSCManagerW(None, None, 0x0001)                     # SC_MANAGER_CONNECT
    if not gestor:
        return {**_no("sin_permiso", "Windows no deja hablar con sus servicios sin administrador."),
                "_auditoria": auditoria}
    try:
        mango = advapi.OpenServiceW(ctypes.c_void_p(gestor), real, 0x0010 | 0x0020 | 0x0004)
        if not mango:
            return {**_no("sin_permiso", f"Para tocar «{visible}» hace falta administrador, y Morgan no se "
                                         "eleva. Puedes hacerlo tú desde Servicios."), "_auditoria": auditoria}
        try:
            estado_bruto = (ctypes.c_ulong * 7)()
            pasos = ["parar", "iniciar"] if accion == "reiniciar" else [accion]
            for paso in pasos:
                if paso == "parar" and psutil.win_service_get(real).status() != "stopped":
                    if not advapi.ControlService(ctypes.c_void_p(mango), 1, estado_bruto):   # STOP
                        return {**_no("sin_permiso", f"Windows no dejó parar «{visible}» "
                                      f"(error {ctypes.GetLastError()})."), "_auditoria": auditoria}
                elif paso == "iniciar" and psutil.win_service_get(real).status() != "running":
                    if not advapi.StartServiceW(ctypes.c_void_p(mango), 0, None):
                        return {**_no("sin_permiso", f"Windows no dejó iniciar «{visible}» "
                                      f"(error {ctypes.GetLastError()})."), "_auditoria": auditoria}
                objetivo = _ESTADOS_DE[paso]
                for _ in range(60):
                    if psutil.win_service_get(real).status() == objetivo:
                        break
                    time.sleep(0.25)
                else:
                    return {**_no("no_verificada", f"«{visible}» no llegó a {objetivo} en 15 s."),
                            "_auditoria": auditoria}
        finally:
            advapi.CloseServiceHandle(ctypes.c_void_p(mango))
    finally:
        advapi.CloseServiceHandle(ctypes.c_void_p(gestor))
    final = psutil.win_service_get(real).status()
    return {"success": True, "data": {"servicio": real, "visible": visible, "estado": final,
                                      "comprobado": f"el servicio está {final}"},
            "error": None, "_auditoria": auditoria}


SERVICIOS = {"service_control": service_control}
