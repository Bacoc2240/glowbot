#!/usr/bin/env bash
# Arnes de mutacion del paquete de conversaciones mas cortas. Separador: ~
#
# Antes de ejecutarlo:
#   mkdir -p /tmp/l18/asistente /tmp/l18/comandos /tmp/l18/templates
#   cp asistente/services.py asistente/models.py /tmp/l18/asistente/
#   cp asistente/management/commands/costos_ia.py /tmp/l18/comandos/
#   cp templates/web/chat.html /tmp/l18/templates/
#
# Advertencia honesta sobre el alcance: la mitad de este paquete son REGLAS
# DE PROMPT, y ninguna prueba puede demostrar que acorten la conversacion.
# Lo unico que las mutaciones comprueban aqui es que si alguien borra una
# regla, una prueba lo nota. Que la conversacion se acorte de verdad solo se
# ve en produccion, con `costos_ia` y el contador de llamadas.
set -u
T=asistente.tests.MenosTurnosPorConversacionTest
C=asistente.tests.ComandoCostosIaTest
W=web.tests.SaludoDelChatTests
if   [ -x .venv/bin/python ];         then PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
else
  echo "ERROR: no encuentro el interprete de .venv. Activa el entorno." >&2
  exit 1
fi
restaurar() {
  cp /tmp/l18/asistente/services.py asistente/services.py
  cp /tmp/l18/asistente/models.py asistente/models.py
  cp /tmp/l18/comandos/costos_ia.py asistente/management/commands/costos_ia.py
  cp /tmp/l18/templates/chat.html templates/web/chat.html
}
trap restaurar EXIT INT TERM
MUTACIONES=(
"Los datos vuelven a pedirse de uno en uno~asistente/services.py~   nombre del cliente y número de teléfono. Pide en UN SOLO mensaje todo lo~   nombre del cliente y número de teléfono. Pide lo que falte, un dato a la vez.~$T.test_los_datos_que_faltan_se_piden_juntos"
"Vuelve el turno de confirmacion de mas~asistente/services.py~   agendar. NO pidas una confirmacion adicional del estilo \"¿confirmo~   agendar. Pide primero que el cliente lo confirme del estilo \"¿confirmo~$T.test_no_se_pide_una_confirmacion_de_mas"
"Se vuelve a preguntar por una eleccion que no existe~asistente/services.py~   Si el sistema devuelve UNA SOLA persona, no hay nada que elegir: no~   Si el sistema devuelve varias personas, pregunta con cual: no~$T.test_no_se_pregunta_por_una_eleccion_que_no_existe"
"Se pierde la prohibicion de elegir profesional~asistente/services.py~15. NUNCA elijas tu el profesional.~15. Puedes elegir tu el profesional.~$T.test_no_se_pregunta_por_una_eleccion_que_no_existe"
"Las llamadas dejan de contarse~asistente/services.py~            conv.llamadas_modelo += 1~            pass~$T.test_cada_llamada_al_modelo_queda_contada,$T.test_un_turno_con_intencion_cuenta_las_dos_llamadas"
"El contador no se guarda en el turno normal~asistente/services.py~        conv.save(update_fields=[\"mensajes\", \"tokens_entrada\", \"tokens_salida\",\n                                 \"llamadas_modelo\", \"telefono_cliente\",\n                                 \"actualizado_en\"])~        conv.save(update_fields=[\"mensajes\", \"tokens_entrada\", \"tokens_salida\",\n                                 \"telefono_cliente\",\n                                 \"actualizado_en\"])~$T.test_cada_llamada_al_modelo_queda_contada"
"El contador no se guarda cuando el turno se rompe~asistente/services.py~        conv.save(update_fields=[\"tokens_entrada\", \"tokens_salida\",\n                                 \"llamadas_modelo\", \"actualizado_en\"])~        conv.save(update_fields=[\"tokens_entrada\", \"tokens_salida\",\n                                 \"actualizado_en\"])~$T.test_las_llamadas_de_un_turno_roto_tambien_cuentan"
"El costo por cita cuenta tambien las citas del panel~asistente/management/commands/costos_ia.py~                establecimiento=est, canal=Cita.Canal.IA, creado_en__gte=desde,~                establecimiento=est, creado_en__gte=desde,~$C.test_el_costo_por_cita_solo_cuenta_las_de_la_ia"
"La tarifa de salida se cobra como la de entrada~asistente/management/commands/costos_ia.py~                     + salida * opciones[\"usd_salida\"]) / 1_000_000~                     + salida * opciones[\"usd_entrada\"]) / 1_000_000~$C.test_informa_llamadas_tokens_y_costo_por_establecimiento"
"Las tarifas dejan de ser parametros~asistente/management/commands/costos_ia.py~            costo = (entrada * opciones[\"usd_entrada\"]~            costo = (entrada * 1.0~$C.test_las_tarifas_son_parametros"
"Un establecimiento sin citas de IA revienta el informe~asistente/management/commands/costos_ia.py~        return f\"{a / b:.{decimales}f}\" if b else \"—\"~        return f\"{a / b:.{decimales}f}\"~$C.test_sin_citas_de_ia_el_informe_no_revienta"
"El saludo vuelve a pedir un solo dato~templates/web/chat.html~¿Cuál deseas agendar? Si ya sabes qué día te sirve, dímelo en el mismo mensaje.~¿Cuál deseas agendar?~$W.test_el_saludo_invita_a_decir_tambien_el_dia"
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
