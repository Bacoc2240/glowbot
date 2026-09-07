#!/usr/bin/env bash
# Arnes de mutacion del paquete de demo publico. Separador: ~
#
# Antes de ejecutarlo:
#   mkdir -p /tmp/l14/negocios /tmp/l14/facturacion /tmp/l14/asistente \
#            /tmp/l14/web /tmp/l14/templates
#   cp negocios/servicios_demo.py /tmp/l14/negocios/
#   cp facturacion/services.py    /tmp/l14/facturacion/
#   cp asistente/api.py           /tmp/l14/asistente/
#   cp web/views.py               /tmp/l14/web/
#   cp templates/web/demo_panel.html templates/web/chat.html /tmp/l14/templates/
set -u
T=negocios.tests_demo
PY=.venv/bin/python
restaurar() {
  cp /tmp/l14/negocios/servicios_demo.py negocios/servicios_demo.py
  cp /tmp/l14/facturacion/services.py    facturacion/services.py
  cp /tmp/l14/asistente/api.py           asistente/api.py
  cp /tmp/l14/web/views.py               web/views.py
  cp /tmp/l14/templates/demo_panel.html  templates/web/demo_panel.html
  cp /tmp/l14/templates/chat.html        templates/web/chat.html
}
trap restaurar EXIT INT TERM
MUTACIONES=(
"El demo vuelve a caducar como un cliente cualquiera~facturacion/services.py~        if establecimiento.es_demo:\n            return True~        if False:\n            return True~$T.ExencionSuspensionTest.test_el_demo_conserva_acceso_aunque_su_suscripcion_este_vencida"
"La suspension marca el demo en base de datos~facturacion/services.py~            .exclude(establecimiento__es_demo=True)~~$T.ExencionSuspensionTest.test_suspender_vencidas_no_toca_al_demo"
"El reseteo alcanza a los negocios reales~negocios/servicios_demo.py~    demos = Establecimiento.objects.filter(es_demo=True)~    demos = Establecimiento.objects.all()~$T.ReseteoTest.test_no_toca_las_citas_de_un_negocio_real"
"El reseteo borra por reloj y no por antiguedad~negocios/servicios_demo.py~    corte = ahora - timedelta(hours=HORAS_VIDA)~    corte = ahora~$T.ReseteoTest.test_no_borra_lo_recien_creado"
"La siembra vuelve a fechas del pasado~negocios/servicios_demo.py~    dias, dia = [], timezone.localdate() + timedelta(days=1)~    dias, dia = [], timezone.localdate() - timedelta(days=30)~$T.SiembraTest.test_la_agenda_sembrada_esta_toda_en_el_futuro"
"El demo queda sin agenda ocupada y el asistente nunca negocia~negocios/servicios_demo.py~        patron = OCUPACION.get(indice_dia, {})~        patron = {}~$T.SiembraTest.test_queda_agenda_ocupada_para_que_el_asistente_tenga_que_negociar"
"El propietario del demo recupera una contrasena usable~negocios/servicios_demo.py~    usuario.set_unusable_password()\n    usuario.is_active = False~    usuario.set_password(\"demo12345\")\n    usuario.is_active = True~$T.SiembraTest.test_el_propietario_no_puede_iniciar_sesion"
"El panel espejo se abre para cualquier establecimiento~web/views.py~        Establecimiento, slug=slug, activo=True, es_demo=True)~        Establecimiento, slug=slug, activo=True)~$T.PanelEspejoTest.test_el_panel_de_un_negocio_real_es_404"
"El panel espejo pinta el telefono del cliente~templates/web/demo_panel.html~            {{ cita.cliente.nombre }} · con {{ cita.profesional.nombre }}~            {{ cita.cliente.nombre }} ({{ cita.cliente.telefono }}) · con {{ cita.profesional.nombre }}~$T.PanelEspejoTest.test_no_expone_ningun_telefono"
"El panel espejo deja de filtrar por establecimiento~web/views.py~            Cita.objects\n            .del_establecimiento(est)\n            .filter(estado=Cita.Estado.CONFIRMADA)~            Cita.objects\n            .filter(estado=Cita.Estado.CONFIRMADA)~$T.PanelEspejoTest.test_no_muestra_citas_de_otro_establecimiento"
"El tope por sesion desaparece~asistente/api.py~    if conv and len(conv.mensajes) >= LIMITE_MENSAJES_SESION_DEMO:~    if False:~$T.TopesDemoTest.test_la_sesion_larga_se_corta"
"El tope diario de tokens desaparece~asistente/api.py~    if gastados >= TOPE_TOKENS_DIA_DEMO:~    if False:~$T.TopesDemoTest.test_el_tope_diario_de_tokens_corta"
"Los topes del demo se aplican tambien a quien paga~asistente/api.py~    if not est.es_demo:\n        return None~    if False:\n        return None~$T.TopesDemoTest.test_el_tope_no_alcanza_a_un_negocio_real"
"El tope diario cuenta las conversaciones de todos los tenants~asistente/api.py~    consumo = ConversacionIA.objects.filter(\n        establecimiento=est,~    consumo = ConversacionIA.objects.filter(~$T.TopesDemoTest.test_el_tope_diario_solo_cuenta_el_consumo_de_este_demo"
"Las etiquetas de calendario quedan invertidas~templates/web/chat.html~>Guardar en Google Calendar (Android)</a>~>Guardar en Calendario (iPhone)</a>~$T.BotonesCalendarioTest.test_el_enlace_de_iphone_es_el_ics"
"Desaparece el boton de consultar citas~templates/web/chat.html~      Mis citas~      Nada~$T.ConsultaDeCitasTest.test_el_chat_ofrece_el_boton_de_consulta"
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
