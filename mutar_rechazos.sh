#!/usr/bin/env bash
# Arnes de mutacion del guardian de rechazos. Separador: ~
#
# Antes de ejecutarlo:
#   mkdir -p /tmp/l16/asistente
#   cp asistente/services.py /tmp/l16/asistente/
set -u
T=asistente.tests_rechazos
# Interprete del entorno virtual. En Linux y macOS esta en bin/; en
# Windows (Git Bash) esta en Scripts/. Se detecta en vez de fijarse para
# que el arnes corra igual en las dos maquinas del proyecto.
if   [ -x .venv/bin/python ];         then PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
else
  echo "ERROR: no encuentro el interprete de .venv. Activa el entorno." >&2
  exit 1
fi
restaurar() {
  cp /tmp/l16/asistente/services.py asistente/services.py
}
trap restaurar EXIT INT TERM
MUTACIONES=(
"El guardian desaparece: la prosa cierra el turno tras un rechazo~asistente/services.py~                if intento_rechazado:~                if False:~$T.RechazoNoCierraElTurnoTest.test_id_obsoleto_y_prosa_no_mienten_al_cliente"
"La bandera nunca se levanta~asistente/services.py~            intento_rechazado = feedback.startswith(MARCA_RECHAZO)~            intento_rechazado = False~$T.RechazoNoCierraElTurnoTest.test_id_obsoleto_y_prosa_no_mienten_al_cliente"
"La bandera se queda pegada y bloquea los flujos legitimos~asistente/services.py~            intento_rechazado = feedback.startswith(MARCA_RECHAZO)~            intento_rechazado = True~$T.RealimentacionInformativaTest.test_varias_citas_el_modelo_pregunta_y_cierra,$T.LimiteDelGuardianTest.test_el_slot_ocupado_sigue_ofreciendo_alternativas"
"El guardian no devuelve el control al modelo~asistente/services.py~                    continue\n                # Red 2~                    pass\n                # Red 2~$T.RechazoNoCierraElTurnoTest.test_id_obsoleto_y_prosa_no_mienten_al_cliente"
"El rechazo por id ya cancelado pierde su marca~asistente/services.py~                            return None, _rechazo(\n                                f\"La cita {cita_id} ya estaba cancelada; ese id \"~                            return None, (\n                                f\"La cita {cita_id} ya estaba cancelada; ese id \"~$T.RechazoNoCierraElTurnoTest.test_id_obsoleto_y_prosa_no_mienten_al_cliente"
"El rechazo generico por id pierde su marca~asistente/services.py~                        return None, _rechazo(\n                            \"Ese cita_id no corresponde a ninguna cita \"~                        return None, (\n                            \"Ese cita_id no corresponde a ninguna cita \"~$T.RechazoPorIdAjenoTest.test_un_id_que_no_existe_tambien_bloquea_el_cierre_en_prosa"
"El mensaje vuelve a ser generico y no nombra el caso~asistente/services.py~                                f\"La cita {cita_id} ya estaba cancelada; ese id \"~                                f\"Ese id no sirve. \"~$T.RechazoNoCierraElTurnoTest.test_el_rechazo_dice_que_el_id_ya_estaba_cancelado"
"El rechazo no lista las citas vigentes~asistente/services.py~                                f\"Las citas confirmadas ahora son: {opciones}. \"~                                \"\"~$T.RechazoNoCierraElTurnoTest.test_el_rechazo_dice_que_el_id_ya_estaba_cancelado"
"La marca [RECHAZO] se le escapa al cliente~asistente/services.py~                feedback = feedback[len(MARCA_RECHAZO):]~                pass~$T.RealimentacionInformativaTest.test_la_marca_no_se_le_inyecta_al_modelo"
"El id ya cancelado se busca en toda la tabla, sin filtrar tenant~asistente/services.py~                        previa = (Cita.objects.del_establecimiento(establecimiento)\n                                  .filter(pk=cita_id, cliente__telefono=telefono)~                        previa = (Cita.objects\n                                  .filter(pk=cita_id, cliente__telefono=telefono)~$T.RechazoPorIdAjenoTest.test_no_mira_las_citas_de_otro_establecimiento"
)
desde=${1:-0}; hasta=${2:-${#MUTACIONES[@]}}
total=0; nomuerden=0
for ((i=desde; i<hasta && i<${#MUTACIONES[@]}; i++)); do
  IFS='~' read -r titulo archivo viejo nuevo pruebas <<< "${MUTACIONES[$i]}"
  restaurar
  VIEJO="$viejo" NUEVO="$nuevo" ARCHIVO="$archivo" $PY - <<'PYAP'
import os, sys, pathlib
p = pathlib.Path(os.environ["ARCHIVO"]); b = p.read_bytes(); t = b.decode("utf-8")
fin = "\r\n" if b"\r\n" in b else "\n"
viejo = os.environ["VIEJO"].replace("\\n", fin)
nuevo = os.environ["NUEVO"].replace("\\n", fin)
if viejo not in t:
    print("NO_APLICABLE"); sys.exit(3)
p.write_bytes(t.replace(viejo, nuevo, 1).encode("utf-8"))
PYAP
  rc=$?
  if [ $rc -eq 3 ]; then
    echo ""; echo "[$i] $titulo"; echo "   !! NO APLICABLE"; nomuerden=$((nomuerden+1)); continue
  elif [ $rc -ne 0 ]; then
    echo ""; echo "ERROR: el aplicador de mutaciones fallo (codigo $rc)." >&2
    echo "Ninguna mutacion se aplico. El resultado NO es valido." >&2
    restaurar; exit 1
  fi
  echo ""; echo "[$i] $titulo"
  IFS=',' read -ra lista <<< "$pruebas"
  for prueba in "${lista[@]}"; do
    total=$((total+1))
    if timeout 180 $PY manage.py test -v 0 --keepdb "$prueba" >/dev/null 2>&1; then
      echo "   NO MUERDE  ${prueba##*.}"; nomuerden=$((nomuerden+1))
    else
      echo "   MUERDE     ${prueba##*.}"
    fi
  done
done
restaurar
echo ""
echo "===================================================================="
if [ $nomuerden -gt 0 ]; then echo "FALLO: $nomuerden de $total no mordieron"; exit 1; fi
echo "OK: las $total comprobaciones mordieron."
