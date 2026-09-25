#!/usr/bin/env bash
# Arnes de mutacion del paquete de horizonte compartido. Separador: ~
#
# Antes de ejecutarlo:
#   mkdir -p /tmp/l21/agenda /tmp/l21/asistente
#   cp agenda/services.py /tmp/l21/agenda/
#   cp asistente/services.py asistente/api.py /tmp/l21/asistente/
set -u
H=asistente.tests.ElChatNoInventaElFinDeLaAgendaTest
A=asistente.tests.AgendaPublicaTest
if   [ -x .venv/bin/python ];         then PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
else
  echo "ERROR: no encuentro el interprete de .venv. Activa el entorno." >&2
  exit 1
fi
restaurar() {
  cp /tmp/l21/agenda/services.py agenda/services.py
  cp /tmp/l21/asistente/services.py asistente/services.py
  cp /tmp/l21/asistente/api.py asistente/api.py
}
trap restaurar EXIT INT TERM
MUTACIONES=(
"Cada camino vuelve a tener su propio horizonte~asistente/api.py~    DIAS_MAX_ADELANTE = DIAS_MAX_AGENDA~    DIAS_MAX_ADELANTE = 30~$H.test_el_alcance_sale_de_la_misma_constante_que_la_rejilla"
"El horizonte compartido desaparece y se puede recorrer un ano de agenda ajena~agenda/services.py~DIAS_MAX_AGENDA = 90~DIAS_MAX_AGENDA = 3650~$A.test_una_fecha_demasiado_lejana_se_rechaza"
"El prompt deja de decir hasta donde llega la agenda~asistente/services.py~ALCANCE DE LA AGENDA: el negocio recibe reservas hasta el {hasta}. Por este~SIN ALCANCE. Por este~$H.test_el_prompt_dice_hasta_donde_llega_la_agenda"
"El alcance se calcula sobre la tabla y no sobre la agenda real~asistente/services.py~        hasta = fecha_larga(ahora.date() + timedelta(days=DIAS_MAX_AGENDA))~        hasta = fecha_larga(ahora.date() + timedelta(days=14))~$H.test_el_prompt_dice_hasta_donde_llega_la_agenda"
"La tabla del prompt engorda hasta el horizonte entero~asistente/services.py~    def _calendario(hoy, dias: int = 14) -> str:~    def _calendario(hoy, dias: int = 90) -> str:~$H.test_la_tabla_sigue_siendo_corta"
"Se le permite volver a decir que la fecha no existe~asistente/services.py~   fecha \"no existe\", \"no esta en nuestro calendario\" o que \"los dias~   fecha esta lejos o que \"los dias~$H.test_se_le_prohibe_decir_que_la_fecha_no_existe"
"Se pierde la salida hacia el calendario de la pantalla~asistente/services.py~   calendario de la pantalla, donde puede elegir el dia y la hora.~   proximas dos semanas y nada mas por ahora.~$H.test_se_le_dice_a_donde_mandar_al_cliente"
"Vuelve el Markdown en las respuestas del chat~asistente/services.py~20. Escribe en TEXTO PLANO. Nada de asteriscos para negrita, almohadillas~20. Puedes usar Markdown: asteriscos para negrita, almohadillas~$H.test_se_le_prohibe_el_markdown"
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
