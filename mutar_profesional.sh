#!/usr/bin/env bash
# Arnes de mutacion del paquete 57: el profesional antes del dia, y la
# portada sin "asistente de inteligencia artificial". Separador: ~
#
# Antes de ejecutarlo, con el paquete ya copiado:
#   mkdir -p /tmp/l22/asistente /tmp/l22/templates
#   cp asistente/api.py /tmp/l22/asistente/
#   cp templates/web/chat.html templates/web/portada.html /tmp/l22/templates/
#
# Las mutaciones de plantilla comprueban que el enganche esta escrito, no
# que el JavaScript funcione (Django no lo ejecuta). Lo que protege el
# comportamiento son las mutaciones del endpoint, las cuatro primeras.
set -u
E=asistente.tests.EligeElProfesionalTest
P=web.tests.EligeElProfesionalPantallaTests
G=web.tests.PantallaGuiadaTests
L=web.tests.PortadaAutoservicioTests
if   [ -x .venv/bin/python ];         then PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
else
  echo "ERROR: no encuentro el interprete de .venv. Activa el entorno." >&2
  exit 1
fi
restaurar() {
  cp /tmp/l22/asistente/api.py asistente/api.py
  cp /tmp/l22/templates/chat.html templates/web/chat.html
  cp /tmp/l22/templates/portada.html templates/web/portada.html
}
trap restaurar EXIT INT TERM
MUTACIONES=(
"El selector se arma con la respuesta ya filtrada y se encoge al usarlo~asistente/api.py~            \"equipo\": plantilla,~            \"equipo\": [{\"profesional_id\": p.id, \"profesional\": p.nombre} for p, _ in equipo],~$E.test_filtrar_no_recorta_el_equipo"
"La tira vuelve a contar a todo el equipo aunque se elija a alguien~asistente/api.py~            equipo = [(p, _) for p, _ in equipo if str(p.id) == str(pedido)]~            pass~$E.test_filtrada_la_tira_cuenta_solo_a_esa_persona,$E.test_filtrada_el_primer_dia_con_cupo_es_el_de_esa_persona"
"El selector ofrece a quien no presta el servicio~asistente/api.py~            \"equipo\": plantilla,~            \"equipo\": [{\"profesional_id\": p.id, \"profesional\": p.nombre} for p in Profesional.objects.filter(establecimiento=est)],~$E.test_el_equipo_solo_trae_a_los_asignados"
"El endpoint publico suelta un dato de mas del profesional~asistente/api.py~        plantilla = [{\"profesional_id\": p.id, \"profesional\": p.nombre}~        plantilla = [{\"profesional_id\": p.id, \"profesional\": p.nombre, \"activo\": p.activo}~$E.test_el_equipo_no_expone_mas_que_el_nombre"
"El profesional pierde su pregunta propia~templates/web/chat.html~        <h2>Elige el profesional</h2>~~$P.test_el_profesional_tiene_su_pregunta_antes_del_dia"
"El selector vuelve a salir de las horas recibidas~templates/web/chat.html~          <template x-for=\"p in equipo\"~          <template x-for=\"p in profesionales\"~$P.test_el_selector_sale_del_equipo_completo"
"La pantalla no guarda el equipo de la respuesta~templates/web/chat.html~          this.equipo = d.equipo || [];~~$P.test_el_selector_sale_del_equipo_completo"
"Se pregunta por el profesional aunque solo haya uno~templates/web/chat.html~      <div class=\"tarjeta\" x-show=\"servicioId && equipo.length > 1\"~      <div class=\"tarjeta\" x-show=\"servicioId\"~$G.test_el_filtro_de_profesional_solo_sale_si_hay_a_quien_elegir"
"Elegir a alguien ya no vuelve a pedir la agenda filtrada~templates/web/chat.html~          + (this.profesionalId === null ? \"\" : \"&profesional_id=\" + this.profesionalId));~);~$P.test_elegir_a_alguien_vuelve_a_pedir_la_agenda_filtrada"
"El boton filtra solo en la pantalla, como antes~templates/web/chat.html~@click=\"elegirProfesional(p.profesional_id)\"~@click=\"profesionalId = p.profesional_id\"~$P.test_elegir_a_alguien_vuelve_a_pedir_la_agenda_filtrada"
"La portada vuelve a presentarse como asistente de IA~templates/web/portada.html~    GlowBot le da a tu negocio una página propia donde tus clientes eligen el~    GlowBot es un asistente de inteligencia artificial que atiende por chat. Tus clientes eligen el~$L.test_no_se_presenta_como_asistente_de_inteligencia_artificial"
"La vista previa de WhatsApp sigue prometiendo el chat~templates/web/portada.html~Tus clientes eligen servicio, dia y hora con unos toques~Un asistente de IA atiende por chat~$L.test_no_se_presenta_como_asistente_de_inteligencia_artificial"
"El paso dos vuelve a decir que el cliente conversa~templates/web/portada.html~El cliente entra y agenda con unos toques.~El cliente entra y ya está conversando.~$L.test_no_se_presenta_como_asistente_de_inteligencia_artificial"
"El asistente desaparece de la portada~templates/web/portada.html~Para lo que no cabe en los botones, un~Y un~$L.test_el_asistente_se_nombra_como_respaldo"
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
