; Los ganchos del instalador de Morgan para Windows (Tauri los incluye al principio del script).
!include LogicLib.nsh

; Donde guarda su estado el agente (como src/agente/estado.py; MORGAN_AGENTE_DIR en las pruebas).
!macro MORGAN_CARPETA_DEL_AGENTE salida
  ReadEnvStr ${salida} MORGAN_AGENTE_DIR
  ${If} ${salida} == ""
    StrCpy ${salida} "$LOCALAPPDATA\Morgan\agente"
  ${EndIf}
!macroend

; --- Actualizar sin desemparejar (5.1) -----------------------------------------------------
;
; Al instalar encima de una version anterior, la pagina "Ya esta instalado" ofrece, marcado por
; defecto, "Desinstalar antes de instalar": ejecuta el DESINSTALADOR VIEJO, y su gancho
; desempareja el agente que emparejo el programa y borra su politica. Actualizar costaba volver
; a emparejar y a elegir carpetas y permisos (visto en la plantilla de Tauri antes de publicar la
; 5.1, con mi agente emparejado por la 5.0.1).
;
; Por eso, ANTES DE NINGUNA PAGINA: si el agente es del programa, se aparta su marca
; (programa.json -> programa.json.actualizando), para que el desinstalador viejo lo de por ajeno
; y no lo toque, y se para (sus ficheros estan en la carpeta que se va a sustituir). Al acabar,
; la marca vuelve y el agente arranca desde la version nueva. Si se cancela, igual.

!macro MORGAN_PREPARAR
  Push $0
  Push $1
  !insertmacro MORGAN_CARPETA_DEL_AGENTE $0
  ${If} ${FileExists} "$0\programa.json"
    Delete "$0\programa.json.actualizando"
    Rename "$0\programa.json" "$0\programa.json.actualizando"
    ; El agente de la instalacion anterior (su carpeta, la que recuerda el registro).
    ReadRegStr $1 HKCU "Software\Morgan\Morgan para Windows" ""
    ${If} $1 == ""
      StrCpy $1 $INSTDIR
    ${EndIf}
    ${If} ${FileExists} "$1\agente\morgan-agente\morgan-agente.exe"
      nsExec::ExecToLog '"$1\agente\morgan-agente\morgan-agente.exe" parar'
      Pop $1
    ${EndIf}
  ${EndIf}
  Pop $1
  Pop $0
!macroend

; La marca vuelve y el agente arranca (y el acceso de Inicio apunta) desde `carpeta`. Si esta en
; pausa, `arranque activar` no lo lanza.
!macro MORGAN_RESTAURAR carpeta
  Push $0
  !insertmacro MORGAN_CARPETA_DEL_AGENTE $0
  ${If} ${FileExists} "$0\programa.json.actualizando"
    Rename "$0\programa.json.actualizando" "$0\programa.json"
    ${If} ${FileExists} "${carpeta}\agente\morgan-agente\morgan-agente.exe"
      nsExec::ExecToLog '"${carpeta}\agente\morgan-agente\morgan-agente.exe" arranque activar'
      Pop $0
    ${EndIf}
  ${EndIf}
  Pop $0
!macroend

!define MUI_CUSTOMFUNCTION_GUIINIT MorganAntesDeNada
Function MorganAntesDeNada
  !insertmacro MORGAN_PREPARAR
FunctionEnd

!define MUI_CUSTOMFUNCTION_ABORT MorganSiSeCancela
Function MorganSiSeCancela
  Push $1
  ReadRegStr $1 HKCU "Software\Morgan\Morgan para Windows" ""
  ${If} $1 == ""
    StrCpy $1 $INSTDIR
  ${EndIf}
  !insertmacro MORGAN_RESTAURAR $1
  Pop $1
FunctionEnd

; Sin ventana (/S) no hay paginas ni se ejecuta el desinstalador viejo, pero el agente se para
; igual: sus ficheros se van a sustituir.
!macro NSIS_HOOK_PREINSTALL
  !insertmacro MORGAN_PREPARAR
!macroend

; Al instalar (5.1): el programa arranca solo en la bandeja al iniciar sesion, con la clave Run
; de Windows (por usuario, sin administrador). El agente sigue con su propio acceso de Inicio
; (el vigilante): si la bandeja se cierra, Morgan sigue funcionando.
!macro NSIS_HOOK_POSTINSTALL
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Morgan para Windows" '"$INSTDIR\Morgan.exe" --bandeja'
  !insertmacro MORGAN_RESTAURAR $INSTDIR
!macroend

; Al desinstalar: se quita de la clave Run y, antes de borrar los ficheros, el agente se para,
; desempareja el PC (la nube deja de aceptar su credencial) y se quita del inicio de Windows.
; Quedan los respaldos de los archivos que modifico, como siempre (decision de la 3.8).
;
; SOLO si el agente lo empareja el programa (5.0.1, `--del-programa`). El agente de la linea de
; PowerShell comparte la carpeta del estado: el 2026-10-04, instalar y desinstalar el programa
; en un PC con ese agente lo desemparejo y borro su politica. Si no es del programa (o se esta
; actualizando y la marca esta apartada), no se toca.
!macro NSIS_HOOK_PREUNINSTALL
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Morgan para Windows"
  nsExec::ExecToLog '"$INSTDIR\agente\morgan-agente\morgan-agente.exe" desinstalar --si --del-programa'
!macroend
