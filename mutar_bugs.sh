#!/usr/bin/env bash
set -u
A=asistente.tests
N=negocios.tests
W=web.tests
PY=.venv/bin/python
restaurar() {
  cp /tmp/l6/asistente/services.py asistente/services.py
  cp /tmp/l6/asistente/api.py asistente/api.py
  cp /tmp/l6/negocios/api.py negocios/api.py
  cp /tmp/l6/templates/web/servicios.html templates/web/servicios.html
}
trap restaurar EXIT INT TERM

MUTACIONES=(
"El asistente vuelve a cancelar la mas proxima sin preguntar|asistente/services.py|                    if len(citas) > 1:|                    if False:|$A.CancelarLaCitaCorrectaTest.test_con_dos_citas_no_se_cancela_ninguna"
"El cita_id se busca en toda la tabla y no en las del telefono|asistente/services.py|                    cita = next((c for c in citas if c.id == cita_id), None)|                    cita = Cita.objects.filter(pk=cita_id).first()|$A.CancelarLaCitaCorrectaTest.test_no_se_puede_cancelar_la_cita_de_otra_persona"
"La consulta vuelve a informar solo de la proxima|asistente/services.py|                detalle = \"; \".join(|                detalle = \"; \".join(list(|$A.CancelarLaCitaCorrectaTest.test_la_consulta_informa_de_todas_las_citas"
"Deja de inyectarse el estado real en el turno|asistente/services.py|        if conv.telefono_cliente:|        if False:|$A.EstadoRealInyectadoTest.test_el_estado_viaja_en_el_turno_cuando_se_conoce_el_telefono"
"El telefono deja de recordarse entre turnos|asistente/services.py|            if telefono and conv.telefono_cliente != telefono:|            if False:|$A.EstadoRealInyectadoTest.test_el_telefono_se_recuerda_al_darlo"
"El resumen deja de distinguir cancelada de confirmada|asistente/services.py|            estado = (\"CONFIRMADA\" if c.estado == Cita.Estado.CONFIRMADA\\n                      else \"CANCELADA\")|            estado = \"CONFIRMADA\"|$A.EstadoRealInyectadoTest.test_el_resumen_distingue_cancelada_de_confirmada"
"Los estados inyectados se acumulan en el historial guardado|asistente/services.py|        conv.mensajes = [\\n            m for m in historial\\n            if not m.get(\"content\", \"\").startswith(f\"[SISTEMA] {MARCA_ESTADO}\")\\n        ]|        conv.mensajes = historial|$A.EstadoRealInyectadoTest.test_los_estados_inyectados_no_se_acumulan_en_el_historial"
"Se retira la regla 12 (no afirmar cambios de estado)|asistente/services.py|   digas que una cita \"queda confirmada\"|   digas que una cita ha cambiado|$A.EstadoRealInyectadoTest.test_el_prompt_prohibe_afirmar_cambios_de_estado"
"Se retira la regla 13 (una cita cancelada no se reactiva)|asistente/services.py|13. Una cita CANCELADA no se puede reactivar|13. Una cita CANCELADA es reversible|$A.EstadoRealInyectadoTest.test_el_prompt_prohibe_afirmar_cambios_de_estado"
"Se retira la regla 14 (preguntar cual antes de cancelar)|asistente/services.py|14. Antes de cancelar, si el cliente tiene mas de una cita, pregunta cual|14. Antes de cancelar, elige la mas proxima|$A.EstadoRealInyectadoTest.test_el_prompt_prohibe_afirmar_cambios_de_estado"
"La zona publica vuelve a cancelar la mas proxima|asistente/api.py|            if len(citas) > 1:|            if False:|$A.CancelacionPublicaSinAmbiguedadTest.test_con_varias_citas_no_cancela_ninguna"
"En la zona publica el id se busca en toda la tabla|asistente/api.py|            cita = next((c for c in citas if c.id == cita_id), None)|            from agenda.models import Cita as _C; cita = _C.objects.filter(pk=cita_id).first()|$A.CancelacionPublicaSinAmbiguedadTest.test_no_se_puede_cancelar_la_cita_de_otra_persona"
"Borrar un servicio con historial vuelve a dar error 500|negocios/api.py|        except ProtectedError:|        except ZeroDivisionError:|$N.EliminarServicioTest.test_nunca_devuelve_un_error_de_servidor"
"La respuesta vuelve a ser muda e indistinguible|negocios/api.py|            \"detalle\": f\"«{nombre}» se eliminó.\",|            \"detalle\": detalle_generico(),|$N.EliminarServicioTest.test_la_respuesta_distingue_los_dos_desenlaces"
"Una cita cancelada vuelve a contar como cita por atender|negocios/api.py|        ).exclude(estado__startswith=\"cancelada\").count()|        ).count()|$N.EliminarServicioTest.test_una_cita_cancelada_no_cuenta_como_cita_por_atender"
"El panel deja de marcar los servicios desactivados|templates/web/servicios.html|<span class=\"chip chip-inactivo\" x-show=\"!s.activo\">|<span class=\"chip\" x-show=\"true\">|$W.PantallaServicioDesactivadoTests.test_un_servicio_desactivado_se_distingue_a_simple_vista"
"Se pierde el boton de reactivar|templates/web/servicios.html|@click=\"reactivarServicio(s)\"|@nada=\"\"|$W.PantallaServicioDesactivadoTests.test_se_puede_reactivar_desde_la_misma_pantalla"
"El panel ignora lo que respondio el servidor|templates/web/servicios.html|        if (d && d.detalle) this.avisoS = d.detalle;|        void d;|$W.PantallaServicioDesactivadoTests.test_la_pantalla_cuenta_lo_que_respondio_el_servidor"
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
