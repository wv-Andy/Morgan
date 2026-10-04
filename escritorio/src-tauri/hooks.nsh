; Al desinstalar Morgan (5.0): antes de borrar los ficheros, el agente se para, desempareja el
; PC (la nube deja de aceptar su credencial) y se quita del inicio de Windows. Quedan los
; respaldos de los archivos que modificó, como siempre (decisión de la 3.8).
!macro NSIS_HOOK_PREUNINSTALL
  nsExec::ExecToLog '"$INSTDIR\agente\morgan-agente\morgan-agente.exe" desinstalar --si'
!macroend
