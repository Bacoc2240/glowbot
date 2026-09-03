#!/usr/bin/env bash
# Arnes de mutacion del paquete .ics. Separador de campos: ~
#
# Antes de ejecutarlo:
#   mkdir -p /tmp/l11/agenda /tmp/l11/asistente /tmp/l11/web
#   cp agenda/calendario.py   /tmp/l11/agenda/
#   cp asistente/services.py  /tmp/l11/asistente/
#   cp web/views.py           /tmp/l11/web/
set -u
C=agenda.tests.CalendarioDescargableTest
E=asistente.tests.EnlacesDeCalendarioEnLaRespuestaTest
PY=.venv/bin/python
restaurar() {
  cp /tmp/l11/agenda/calendario.py agenda/calendario.py
  cp /tmp/l11/asistente/services.py asistente/services.py
  cp /tmp/l11/web/views.py web/views.py
}
trap restaurar EXIT INT TERM

MUTACIONES=(
"La hora se emite en local en vez de UTC (cita corrida 5 horas)~agenda/calendario.py~_UTC_OFFSET = timedelta(hours=5)~_UTC_OFFSET = timedelta(hours=0)~$C.test_la_hora_local_se_emite_en_utc,$C.test_el_enlace_de_google_lleva_las_mismas_horas"
"El desfase se aplica al reves~agenda/calendario.py~_UTC_OFFSET = timedelta(hours=5)~_UTC_OFFSET = timedelta(hours=-5)~$C.test_la_hora_local_se_emite_en_utc"
"El UID pasa a ser aleatorio: una copia por cada descarga~agenda/calendario.py~        f\"UID:cita-{cita.id}@glowbot.com.co\",~        f\"UID:{timezone.now().timestamp()}@glowbot.com.co\",~$C.test_el_identificador_es_estable"
"El resumen deja de escaparse~agenda/calendario.py~        f\"SUMMARY:{_escapar(titulo)}\",~        f\"SUMMARY:{titulo}\",~$C.test_las_comas_del_servicio_van_escapadas"
"Se deja de plegar a 75 octetos~agenda/calendario.py~    if len(crudo) <= 75:~    if True:~$C.test_ninguna_linea_pasa_de_75_octetos"
"El plegado cuenta caracteres y parte por mitad de un multibyte~agenda/calendario.py~    crudo = linea.encode(\"utf-8\")~    crudo = linea.encode(\"latin-1\", \"replace\")~$C.test_el_plegado_no_parte_un_caracter_por_la_mitad"
"La firma deja de comprobarse: la URL vuelve a ser adivinable~web/views.py~    if not firma_valida(cita_id, firma):~    if False:~$C.test_sin_firma_valida_no_se_entrega,$C.test_la_firma_de_otra_cita_no_sirve"
"La firma no depende de la cita~agenda/calendario.py~    mensaje = f\"cita:{cita_id}\".encode()~    mensaje = \"cita\".encode()~$C.test_la_firma_de_otra_cita_no_sirve"
"Se pierde el aislamiento por establecimiento en la descarga~web/views.py~        pk=cita_id, establecimiento__slug=slug,~        pk=cita_id,~$C.test_no_se_descarga_desde_el_slug_de_otro_establecimiento"
"Una cita cancelada se puede descargar~web/views.py~        estado=Cita.Estado.CONFIRMADA,~~$C.test_una_cita_cancelada_no_se_descarga"
"El nombre del archivo filtra datos del cliente~web/views.py~filename=\"cita-{cita.id}.ics\"~filename=\"cita-{cita.cliente.nombre}-{cita.cliente.telefono}.ics\"~$C.test_el_nombre_del_archivo_no_lleva_datos_del_cliente"
"Los enlaces desaparecen de la respuesta~asistente/services.py~                        \"google\": enlace_google(cita),~~$E.test_la_cita_creada_trae_los_dos_enlaces"
"El enlace del ics se arma con una firma incorrecta~asistente/services.py~{firma_cita(cita.id)}.ics~{firma_cita(cita.id + 1)}.ics~$E.test_el_enlace_del_ics_funciona_de_verdad"
)

desde=${1:-0}; hasta=${2:-${#MUTACIONES[@]}}
total=0; nomuerden=0
for ((i=desde; i<hasta && i<${#MUTACIONES[@]}; i++)); do
  IFS='~' read -r titulo archivo viejo nuevo pruebas <<< "${MUTACIONES[$i]}"
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
    if timeout 120 $PY manage.py test -v 0 --keepdb "$prueba" >/dev/null 2>&1; then
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
