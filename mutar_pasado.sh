#!/usr/bin/env bash
set -u
A=agenda.tests.NoAgendarEnElPasadoTest
S=asistente.tests.AsistenteNoOfreceHorasPasadasTest
W=web.tests.PanelNoAgendaEnElPasadoTests
PY=.venv/bin/python
restaurar() {
  cp /tmp/l8/agenda/services.py agenda/services.py
  cp /tmp/l8/agenda/api.py agenda/api.py
  cp /tmp/l8/asistente/services.py asistente/services.py
}
trap restaurar EXIT INT TERM

MUTACIONES=(
"El calculo deja de mirar el reloj|agenda/services.py|        if dia == ahora.date():|        if False:|$A.test_no_se_ofrecen_horas_que_ya_pasaron,$S.test_el_feedback_no_contiene_horas_pasadas"
"Se pierde la antelacion minima (margen cero)|agenda/services.py|ANTELACION_MINIMA_MIN = 30|ANTELACION_MINIMA_MIN = 0|$A.test_tampoco_se_ofrece_lo_que_empieza_en_diez_minutos"
"El filtro se aplica tambien a los dias futuros|agenda/services.py|        if dia == ahora.date():|        if dia >= ahora.date():|$A.test_en_un_dia_futuro_se_ofrece_la_jornada_entera"
"reservar deja de rechazar el pasado|agenda/services.py|        if dia < ahora.date() or (|        if False and (|$A.test_no_se_puede_reservar_ayer,$A.test_no_se_puede_reservar_una_hora_de_hoy_ya_pasada"
"reservar solo mira la hora y no el dia|agenda/services.py|        if dia < ahora.date() or (|        if (|$A.test_no_se_puede_reservar_ayer"
"El canal manual queda autorizado a agendar en el pasado|agenda/services.py|            and inicio_min < (ahora.hour * 60 + ahora.minute) + antelacion_min|            and antelacion_min > 0 and inicio_min < (ahora.hour * 60 + ahora.minute) + antelacion_min|$A.test_el_canal_manual_tampoco_puede_agendar_en_el_pasado"
"El panel deja de pedir margen cero|agenda/api.py|                antelacion_min=0)|                antelacion_min=30)|$W.test_las_horas_del_panel_incluyen_lo_inmediato"
"El panel devuelve error 500 al agendar en el pasado|agenda/api.py|        except CitaEnElPasado as e:|        except ZeroDivisionError as e:|$W.test_no_devuelve_error_de_servidor"
"El asistente no explica que vuelva a consultar|asistente/services.py|Es una hora que ya paso. Vuelve a consultar la |Esa hora no sirve. |$S.test_al_rechazar_se_le_pide_consultar_de_nuevo"
"La confirmacion vuelve a la hora cruda del JSON|asistente/services.py|f\"{hora_texto(cita.hora_inicio)} con \"|f\"{intencion['hora_inicio']} con \"|$S.test_la_confirmacion_dice_la_hora_en_doce_horas"
)

desde=${1:-0}; hasta=${2:-${#MUTACIONES[@]}}
total=0; nomuerden=0
for ((i=desde; i<hasta && i<${#MUTACIONES[@]}; i++)); do
  IFS='|' read -r titulo archivo viejo nuevo pruebas <<< "${MUTACIONES[$i]}"
  restaurar
  VIEJO="$viejo" NUEVO="$nuevo" ARCHIVO="$archivo" python3 - <<'PYAP'
import os, sys, pathlib
p = pathlib.Path(os.environ["ARCHIVO"]); b = p.read_bytes(); t = b.decode("utf-8")
fin = "\r\n" if b"\r\n" in b else "\n"
viejo = os.environ["VIEJO"].replace("\\n", fin)
nuevo = os.environ["NUEVO"].replace("\\n", fin)
if viejo not in t:
    print("NO_APLICABLE"); sys.exit(3)
p.write_bytes(t.replace(viejo, nuevo, 1).encode("utf-8"))
PYAP
  if [ $? -eq 3 ]; then
    echo ""; echo "[$i] $titulo"; echo "   !! NO APLICABLE"; nomuerden=$((nomuerden+1)); continue
  fi
  echo ""; echo "[$i] $titulo"
  IFS=',' read -ra lista <<< "$pruebas"
  for prueba in "${lista[@]}"; do
    total=$((total+1))
    if timeout 60 $PY manage.py test -v 0 --keepdb "$prueba" >/dev/null 2>&1; then
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
