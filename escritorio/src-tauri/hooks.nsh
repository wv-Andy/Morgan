; Al desinstalar Morgan para Windows: antes de borrar los ficheros, el agente se para,
; desempareja el PC (la nube deja de aceptar su credencial) y se quita del inicio de Windows.
; Quedan los respaldos de los archivos que modifico, como siempre (decision de la 3.8).
;
; SOLO si el agente lo empareja el programa (5.0.1, `--del-programa`). El agente de la linea de
; PowerShell comparte la carpeta del estado: el 2026-10-04, instalar y desinstalar el programa
; en un PC con ese agente lo desemparejo y borro su politica. Si no es del programa, no se toca.
!macro NSIS_HOOK_PREUNINSTALL
  nsExec::ExecToLog '"$INSTDIR\agente\morgan-agente\morgan-agente.exe" desinstalar --si --del-programa'
!macroend
