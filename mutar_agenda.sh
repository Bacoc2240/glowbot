#!/usr/bin/env bash
# Arnes de mutacion del paquete de agenda publica sin modelo. Separador: ~
#
# Antes de ejecutarlo:
#   mkdir -p /tmp/l19/agenda /tmp/l19/asistente
#   cp agenda/services.py /tmp/l19/agenda/
#   cp asistente/api.py asistente/services.py /tmp/l19/asistente/
#
# Cuidado al escribir mutaciones sobre plantillas o cadenas con JavaScript:
# las entradas van entre comillas dobles, asi que bash expande ${...} y
# ejecuta lo que este entre acentos graves. Una mutacion asi se aplica vacia
# y la prueba pasa: parece que la comprobacion no muerde cuando lo que fallo
# fue el arnes. Se eligen fragmentos sin $ ni acentos graves.
set -u
A=asistente.tests.AgendaPublicaTest
C=asistente.tests.CrearCitaSinModeloTest
O=agenda.tests.OfertaUnicaTest
E=asistente.tests.DisponibilidadDeTodoElEquipoTest
if   [ -x .venv/bin/python ];         then PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
else
  echo "ERROR: no encuentro el interprete de .venv. Activa el entorno." >&2
  exit 1
fi
restaurar() {
  cp /tmp/l19/agenda/services.py agenda/services.py
  cp /tmp/l19/asistente/api.py asistente/api.py
  cp /tmp/l19/asistente/services.py asistente/services.py
}
trap restaurar EXIT INT TERM
MUTACIONES=(
"La oferta deja de filtrar por servicio asignado~agenda/services.py~                  .filter(establecimiento=establecimiento, activo=True,\n                          servicios=servicio)~                  .filter(establecimiento=establecimiento, activo=True)~$O.test_no_devuelve_a_quien_no_presta_el_servicio,$A.test_solo_aparecen_los_asignados_al_servicio"
"La oferta incluye profesionales inactivos~agenda/services.py~                  .filter(establecimiento=establecimiento, activo=True,~                  .filter(establecimiento=establecimiento,~$O.test_no_devuelve_a_un_profesional_inactivo"
"Se esconde a quien no tiene horas libres~agenda/services.py~        return [(p, cls.calcular_slots(p, servicio, dia)) for p in equipo]~        return [(p, s) for p in equipo if (s := cls.calcular_slots(p, servicio, dia))]~$O.test_quien_no_tiene_horas_viene_con_la_lista_vacia,$A.test_a_quien_no_tiene_horas_se_le_nombra_igual"
"El chat y la rejilla dejan de compartir la lista~asistente/services.py~        equipo = AgendaService.disponibilidad_por_profesional(\n            establecimiento, servicio, dia)~        equipo = [(p, []) for p in Profesional.objects.filter(\n            establecimiento=establecimiento, activo=True)]~$E.test_no_ofrece_a_quien_no_presta_el_servicio"
"La tira de dias ignora los bloqueos~asistente/api.py~            del_dia = [(p, AgendaService.calcular_slots(p, servicio, dia))~            del_dia = [(p, AgendaService._slots_de_la_jornada(p, servicio, dia))~$A.test_un_dia_bloqueado_aparece_en_cero"
"Todos los dias de la tira devuelven la agenda del primero~asistente/api.py~            dia = desde + timedelta(days=n)~            dia = desde~$A.test_senala_el_primer_dia_con_cupo"
"La cuenta de horas libres de cada dia se pierde~asistente/api.py~            libres = sum(len(h) for _, h in del_dia)~            libres = 0~$A.test_cada_dia_dice_cuantas_horas_libres_tiene,$A.test_sin_bloqueos_el_primer_dia_con_cupo_es_el_primero"
"El primer dia con cupo se asume, no se busca~asistente/api.py~        con_cupo = next((d[\"fecha\"] for d in tira if d[\"libres\"]), None)~        con_cupo = tira[0][\"fecha\"]~$A.test_senala_el_primer_dia_con_cupo,$A.test_sin_cupo_en_todo_el_tramo_no_inventa_un_dia"
"El filtro por profesional abre una puerta de atras~asistente/api.py~            equipo = [(p, _) for p, _ in equipo if str(p.id) == str(pedido)]~            equipo = [(p, []) for p in Profesional.objects.filter(pk=pedido)]~$A.test_un_profesional_que_no_presta_el_servicio_no_cuela_su_agenda"
"El servicio deja de acotarse al negocio del enlace~asistente/api.py~                pk=request.query_params.get(\"servicio_id\"),\n                establecimiento=est, activo=True)~                pk=request.query_params.get(\"servicio_id\"), activo=True)~$A.test_el_servicio_de_otro_negocio_no_devuelve_su_agenda"
"Se aceptan fechas ya pasadas~asistente/api.py~        if desde < hoy:~        if False:~$A.test_una_fecha_pasada_se_rechaza"
"Desaparece el techo de dias hacia adelante~asistente/api.py~        if (desde - hoy).days > self.DIAS_MAX_ADELANTE:~        if False:~$A.test_una_fecha_demasiado_lejana_se_rechaza"
"Desaparece el tope del tramo consultable~asistente/api.py~        if not 1 <= dias <= self.DIAS_MAX_TRAMO:~        if False:~$A.test_un_tramo_desmedido_se_rechaza"
"Una fecha ilegible revienta la peticion~asistente/api.py~            desde = date.fromisoformat(crudo) if crudo else hoy\n        except ValueError:~            desde = date.fromisoformat(crudo) if crudo else hoy\n        except KeyError:~$A.test_una_fecha_ilegible_no_revienta"
"Se agenda sin constancia del consentimiento~asistente/api.py~        if conv is None or conv.consentimiento_en is None:~        if False:~$C.test_sin_consentimiento_no_se_guarda_nada"
"La cita del autoservicio se cuenta como del asistente~asistente/api.py~                canal=Cita.Canal.WEB,~                canal=Cita.Canal.IA,~$C.test_la_cita_queda_en_el_canal_web"
"El hueco ocupado se reporta como error del cliente~asistente/api.py~            return Response({\"error\": str(e)}, status=status.HTTP_409_CONFLICT)~            return Response({\"error\": str(e)}, status=status.HTTP_400_BAD_REQUEST)~$C.test_un_hueco_ya_tomado_devuelve_409"
"El veto y el tope dejan de frenar la reserva~asistente/api.py~                canal=Cita.Canal.WEB,\n            )~                canal=Cita.Canal.WEB, respetar_bloqueo=False, respetar_tope=False,\n            )~$C.test_un_telefono_vetado_no_agenda"
"La jornada deja de respetarse al agendar~asistente/api.py~                canal=Cita.Canal.WEB,\n            )~                canal=Cita.Canal.WEB, respetar_horario=False,\n            )~$C.test_no_se_puede_agendar_fuera_de_la_jornada,$C.test_no_se_puede_agendar_en_un_dia_bloqueado"
"Se agenda con quien no presta el servicio~asistente/api.py~        if ofrecidos and profesional.id not in ofrecidos:~        if False:~$C.test_un_profesional_que_no_presta_el_servicio_se_rechaza"
"El profesional deja de acotarse al negocio del enlace~asistente/api.py~                pk=datos.get(\"profesional_id\"), establecimiento=est, activo=True)~                pk=datos.get(\"profesional_id\"), activo=True)~$C.test_no_se_agenda_con_el_profesional_de_otro_negocio"
"Se acepta una cita sin nombre~asistente/api.py~        if len(nombre) < 2:~        if False:~$C.test_sin_nombre_no_se_agenda"
"El telefono no queda en la sesion para Mis citas~asistente/api.py~        if conv.telefono_cliente != cliente.telefono:~        if False:~$C.test_el_telefono_queda_en_la_sesion_para_mis_citas"
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
