"""
Una ventana de Windows de verdad, propia de las pruebas (4.12): un campo de texto, uno de
contraseña y un botón «Guardar». Para probar el control de la interfaz **sin tocar ninguna
aplicación de quien pasa las pruebas** (medido: el Bloc de notas de Windows 11 abre las
pestañas de la sesión anterior, con sus archivos).
"""

import ctypes
import threading
from ctypes import wintypes

TITULO = "Ventana de prueba de Morgan"
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class _WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class VentanaDePrueba:
    ID_BOTON = 1001

    def __init__(self):
        self.pulsado = threading.Event()
        self.lista = threading.Event()
        self.hwnd = None
        self.campo = None
        self.clave = None
        self._hilo = threading.Thread(target=self._correr, daemon=True)

    def __enter__(self):
        self._hilo.start()
        assert self.lista.wait(10), "la ventana de prueba no apareció"
        return self

    def __exit__(self, *exc):
        user32 = ctypes.windll.user32
        user32.PostMessageW(self.hwnd, 0x0010, 0, 0)          # WM_CLOSE
        self._hilo.join(5)

    def texto(self, hwnd=None) -> str:
        user32 = ctypes.windll.user32
        hwnd = hwnd or self.campo
        largo = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(largo + 1)
        user32.GetWindowTextW(hwnd, buf, largo + 1)
        return buf.value

    def _correr(self):
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                           wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]

        def procedimiento(hwnd, mensaje, wparam, lparam):
            if mensaje == 0x0111 and (wparam & 0xFFFF) == self.ID_BOTON:     # WM_COMMAND del botón
                self.pulsado.set()
                return 0
            if mensaje == 0x0002:                                              # WM_DESTROY
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, mensaje, wparam, lparam)

        self._proc = WNDPROC(procedimiento)
        instancia = kernel32.GetModuleHandleW(None)
        clase = _WNDCLASS(0, self._proc, 0, 0, instancia, None, None, ctypes.c_void_p(16), None,
                          f"MorganPrueba{id(self)}")
        user32.RegisterClassW(ctypes.byref(clase))
        WS_VISIBLE, WS_CHILD, WS_BORDER, WS_OVERLAPPEDWINDOW = 0x10000000, 0x40000000, 0x00800000, 0x00CF0000
        self.hwnd = user32.CreateWindowExW(0, clase.lpszClassName, TITULO, WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                                           200, 200, 480, 240, None, None, instancia, None)
        self.campo = user32.CreateWindowExW(0, "EDIT", "", WS_CHILD | WS_VISIBLE | WS_BORDER,
                                            20, 20, 300, 28, self.hwnd, None, instancia, None)
        self.clave = user32.CreateWindowExW(0, "EDIT", "", WS_CHILD | WS_VISIBLE | WS_BORDER | 0x0020,  # ES_PASSWORD
                                            20, 60, 300, 28, self.hwnd, None, instancia, None)
        user32.CreateWindowExW(0, "BUTTON", "Guardar", WS_CHILD | WS_VISIBLE, 20, 110, 120, 32,
                               self.hwnd, wintypes.HMENU(self.ID_BOTON), instancia, None)
        user32.SetForegroundWindow(self.hwnd)
        self.lista.set()
        mensaje = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(mensaje), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(mensaje))
            user32.DispatchMessageW(ctypes.byref(mensaje))
