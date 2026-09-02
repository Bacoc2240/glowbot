#!/usr/bin/env bash
# Arnes de mutacion del paquete "lo que ya paso deja de contar".
#
# Reintroduce cada defecto uno por uno y comprueba que la prueba que lo
# cubre FALLA. Una prueba que sigue pasando con el codigo roto no prueba
# nada, y eso ha aparecido en todos los paquetes anteriores.
#
# Antes de ejecutarlo hay que guardar los originales:
#   mkdir -p /tmp/l9/agenda /tmp/l9/asistente /tmp/l9/negocios
#   cp agenda/services.py    /tmp/l9/agenda/
#   cp asistente/services.py /tmp/l9/asistente/
#   cp negocios/api.py       /tmp/l9/negocios/
set -u
A=agenda.tests.QueCuentaComoCitaFuturaTest
C=asistente.tests.NoSeCancelaLoQueYaEmpezoTest
R=asistente.tests.ResumenDeEstadoTest
E=asistente.tests.DisponibilidadDeTodoElEquipoTest
N=negocios.tests.CitasPorAtenderMiranElRelojTest
PY=.venv/bin/python
restaurar() {
  cp /tmp/l9/agenda/services.py agenda/services.py
  cp /tmp/l9/asistente/services.py asistente/services.py
  cp /tmp/l9/negocios/api.py negocios/api.py
}
trap restaurar EXIT INT TERM

MUTACIONES=(
"El filtro vuelve a mirar el calendario y no el reloj~agenda/services.py~            Q(fecha__gt=ahora.date())\\n            | Q(fecha=ahora.date(), hora_inicio__gt=ahora.time())~            Q(fecha__gte=ahora.date())~$A.test_la_cita_de_esta_manana_ya_no_ocupa_cupo,$A.test_el_filtro_parte_las_citas_del_dia_por_el_reloj,$C.test_el_chat_no_cancela_una_cita_que_ya_empezo,$N.test_por_la_tarde_la_cita_de_la_manana_ya_no_esta_por_atender"
"El filtro se pasa de largo y excluye el dia de hoy entero~agenda/services.py~            Q(fecha__gt=ahora.date())\\n            | Q(fecha=ahora.date(), hora_inicio__gt=ahora.time())~            Q(fecha__gt=ahora.date())~$A.test_una_cita_de_mas_tarde_hoy_si_ocupa_cupo,$C.test_una_cita_de_mas_tarde_hoy_si_se_cancela,$N.test_por_la_manana_esa_misma_cita_si_esta_por_atender"
"El corte pasa a ser el final de la cita y no el inicio~agenda/services.py~hora_inicio__gt=ahora.time())~hora_fin__gt=ahora.time())~$A.test_una_cita_en_curso_no_ocupa_cupo,$A.test_futuro_y_ya_empezo_no_dejan_hueco_ni_se_solapan"
"El tope deja de frenar (se desactiva el control de abuso)~agenda/services.py~        if respetar_tope and abiertas >= tope:~        if False:~$A.test_el_tope_sigue_frenando_lo_que_debe_frenar"
"El tope vuelve a contar sin filtrar el pasado~agenda/services.py~        abiertas = cls.solo_futuras(Cita.objects.filter(~        abiertas = (Cita.objects.filter(fecha__gte=timezone.localdate(),~$A.test_la_cita_de_esta_manana_ya_no_ocupa_cupo"
"El listado del asistente deja de filtrar el pasado~asistente/services.py~        return AgendaService.solo_futuras(\\n            Cita.objects.filter(~        return (Cita.objects.filter(fecha__gte=timezone.localdate(),~$C.test_el_chat_no_cancela_una_cita_que_ya_empezo,$C.test_la_puerta_publica_tampoco,$C.test_ni_pasandole_el_identificador_de_esa_cita,$C.test_la_consulta_tampoco_informa_de_las_ya_pasadas"
"El resumen deja de marcar las citas ya pasadas~asistente/services.py~            marca = \" — YA PASO\" if ya_paso else \"\"~            marca = \"\"~$R.test_la_de_esta_manana_aparece_marcada"
"El resumen marca tambien las futuras~asistente/services.py~            marca = \" — YA PASO\" if ya_paso else \"\"~            marca = \" — YA PASO\"~$R.test_la_de_esta_tarde_no_lleva_marca"
"El resumen vuelve a llamar cancelada a una inasistencia~asistente/services.py~f\"— {c.get_estado_display().upper()}{marca}\"~f\"— {'CONFIRMADA' if c.estado == Cita.Estado.CONFIRMADA else 'CANCELADA'}{marca}\"~$R.test_una_inasistencia_no_se_le_presenta_como_cancelada"
"profesional_id vuelve a ser obligatorio~asistente/services.py~                if intencion.get(\"profesional_id\") is None:~                if False:~$E.test_sin_profesional_responde_por_todo_el_equipo,$E.test_si_nadie_lo_presta_no_ofrece_horarios"
"El backend vuelve a elegir por el cliente (responde por uno solo)~asistente/services.py~        equipo = cls._profesionales_que_prestan(establecimiento, servicio)~        equipo = cls._profesionales_que_prestan(establecimiento, servicio)[:1]~$E.test_sin_profesional_responde_por_todo_el_equipo"
"Se callan los que no tienen horas libres~asistente/services.py~                lineas.append(f\"- {p.nombre}: sin horas libres ese dia\")~                pass~$E.test_quien_no_atiende_ese_dia_sale_dicho_asi"
"Se pierde el aislamiento por establecimiento en la lista del equipo~asistente/services.py~                .filter(establecimiento=establecimiento, activo=True,\\n                        servicios=servicio)~                .filter(activo=True, servicios=servicio)~$E.test_no_se_cuela_un_profesional_de_otro_establecimiento"
"Se pierde la asignacion M:N y se ofrece a todo el mundo~asistente/services.py~                .filter(establecimiento=establecimiento, activo=True,\\n                        servicios=servicio)~                .filter(establecimiento=establecimiento, activo=True)~$E.test_no_ofrece_a_quien_no_presta_el_servicio"
"El feedback deja de prohibirle al modelo que elija~asistente/services.py~                      \"libres para que elija; no elijas tu. Ofrece SOLO estas \"~                      \"libres. Ofrece SOLO estas \"~$E.test_le_dice_al_modelo_que_no_elija"
"Un servicio sin nadie asignado devuelve una lista vacia sin explicar~asistente/services.py~        if not equipo:~        if False:~$E.test_si_nadie_lo_presta_no_ofrece_horarios"
"El conteo de citas por atender vuelve a mirar solo la fecha~negocios/api.py~        por_atender = AgendaService.solo_futuras(\\n            instance.citas.all()\\n        ).exclude~        por_atender = instance.citas.filter(fecha__gte=__import__(\"django.utils\", fromlist=[\"timezone\"]).timezone.localdate()).exclude~$N.test_por_la_tarde_la_cita_de_la_manana_ya_no_esta_por_atender"
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
