#!/usr/bin/env bash
# Arnes de mutacion del paquete de aviso de descanso. Separador: ~
#
# Antes de ejecutarlo:
#   mkdir -p /tmp/l17/agenda /tmp/l17/asistente /tmp/l17/negocios /tmp/l17/templates
#   cp agenda/services.py agenda/avisos.py /tmp/l17/agenda/
#   cp asistente/services.py asistente/api.py /tmp/l17/asistente/
#   cp negocios/api.py /tmp/l17/negocios/
#   cp templates/web/chat.html templates/web/horarios.html /tmp/l17/templates/
#
# Una proteccion de este paquete NO tiene mutacion, y conviene decirlo: que
# el motivo del bloqueo no salga al publico no se sostiene en ninguna linea
# que se pueda romper --el campo simplemente no se lee en ningun sitio--. Su
# prueba es una alarma para el futuro, no la verificacion de un mecanismo.
set -u
A=agenda.tests.AvisoDeDescansoTest
I=asistente.tests.ElChatAnunciaElDescansoTest
W=web.tests.PantallaAvisoDescansoTests
if   [ -x .venv/bin/python ];         then PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
else
  echo "ERROR: no encuentro el interprete de .venv. Activa el entorno." >&2
  exit 1
fi
restaurar() {
  cp /tmp/l17/agenda/services.py agenda/services.py
  cp /tmp/l17/agenda/avisos.py agenda/avisos.py
  cp /tmp/l17/asistente/services.py asistente/services.py
  cp /tmp/l17/asistente/api.py asistente/api.py
  cp /tmp/l17/negocios/api.py negocios/api.py
  cp /tmp/l17/templates/chat.html templates/web/chat.html
  cp /tmp/l17/templates/horarios.html templates/web/horarios.html
}
trap restaurar EXIT INT TERM
MUTACIONES=(
"Los dias sueltos tambien se anuncian~agenda/services.py~        ).exclude(fecha=F(\"fecha_fin\")).select_related(\"profesional\")~        ).select_related(\"profesional\")~$A.test_un_dia_suelto_no_se_anuncia"
"Los periodos que ya terminaron se siguen anunciando~agenda/services.py~            fecha_fin__gte=hoy, fecha__lte=limite,~            fecha_fin__gte=hoy - timedelta(days=365), fecha__lte=limite,~$A.test_un_periodo_terminado_no_se_anuncia"
"El horizonte desaparece y se anuncia diciembre en septiembre~agenda/services.py~            fecha_fin__gte=hoy, fecha__lte=limite,~            fecha_fin__gte=hoy, fecha__lte=limite + timedelta(days=365),~$A.test_un_periodo_lejano_no_se_anuncia_todavia"
"Un profesional inactivo cuenta como alternativa~agenda/services.py~            establecimiento=establecimiento, activo=True).order_by(\"id\"))~            establecimiento=establecimiento).order_by(\"id\"))~$A.test_un_profesional_inactivo_no_cuenta_como_alternativa"
"El cierre completo se anuncia como la ausencia de una persona~agenda/services.py~            if atienden:~            if True:~$A.test_si_descansan_todos_el_aviso_es_de_cierre"
"El regreso se asume al dia siguiente (cierre completo)~agenda/services.py~                    cls.primer_dia_con_atencion(p, hasta + timedelta(days=1))~                    hasta + timedelta(days=1)~$A.test_el_regreso_es_el_primer_dia_que_se_atiende,$A.test_el_regreso_salta_un_bloqueo_pegado_al_periodo"
"El regreso se asume al dia siguiente (descanso parcial)~agenda/services.py~                regreso = cls.primer_dia_con_atencion(\n                    descansan[0], hasta + timedelta(days=1))~                regreso = hasta + timedelta(days=1)~$A.test_si_queda_alguien_el_aviso_nombra_la_alternativa"
"El dia de regreso deja de mirar los bloqueos~agenda/services.py~        return bool(cls._huecos_libres(base, cls._bloqueos_del_dia(profesional, dia)))~        return bool(base)~$A.test_el_regreso_salta_un_bloqueo_pegado_al_periodo"
"Un dia lleno de citas se toma por dia cerrado~agenda/services.py~        return bool(cls._huecos_libres(base, cls._bloqueos_del_dia(profesional, dia)))~        return bool(cls._huecos_libres(base, cls._bloqueos_del_dia(profesional, dia) + cls._ocupacion_del_dia(profesional, dia)))~$A.test_un_dia_lleno_de_citas_sigue_siendo_un_dia_de_regreso"
"El limite de busqueda del regreso desaparece~agenda/services.py~        for n in range(limite_dias):~        for n in range(limite_dias * 30):~$A.test_sin_regreso_calculable_el_aviso_no_promete_fecha"
"El cartel omite la fecha de regreso~agenda/avisos.py~    return f\" Volvemos el {fecha_corta(aviso['regreso'])}.\"~    return \"\"~$A.test_el_cartel_de_cierre_dice_fechas_y_regreso"
"La enumeracion de alternativas pierde la 'y'~agenda/avisos.py~    return \", \".join(nombres[:-1]) + f\" y {nombres[-1]}\"~    return \", \".join(nombres)~$A.test_varias_alternativas_se_enumeran_con_y"
"Al modelo se le deja de prohibir que invente~agenda/avisos.py~            \" NO digas el motivo del descanso, no lo supongas y no inventes\"~            \"\"~$A.test_la_linea_del_modelo_le_prohibe_inventar"
"El aviso desaparece del arranque de la pagina publica~asistente/api.py~            \"aviso_descanso\": texto_publico(\n                AgendaService.avisos_de_descanso(est)),~            \"aviso_descanso\": \"\",~$I.test_la_pagina_publica_trae_el_aviso_redactado"
"El modelo deja de recibir el hecho~asistente/services.py~        if aviso:~        if False:~$I.test_al_modelo_se_le_entrega_el_hecho_cada_turno"
"El atajo del equipo no escribe mas que una fila~negocios/api.py~        if equipo_completo:~        if False:~$A.test_todo_el_equipo_escribe_una_fila_por_profesional"
"El atajo alcanza a profesionales de otro establecimiento~negocios/api.py~                establecimiento=prof.establecimiento, activo=True))~                activo=True))~$A.test_el_equipo_no_alcanza_a_otro_establecimiento"
"El aviso de citas solo mira el bloqueo de quien lo creo~negocios/api.py~        } for b in creados for c in AgendaService.citas_bajo_bloqueo(b)]~        } for b in [suyo] for c in AgendaService.citas_bajo_bloqueo(b)]~$A.test_el_aviso_del_equipo_enumera_las_citas_de_todos"
"El chat deja de pintar el cartel~templates/web/chat.html~x-text=\"negocio.aviso_descanso\"~x-text=\"\"~$W.test_el_chat_pinta_el_cartel_del_descanso"
"El cartel se confunde con la letra pequena del aviso legal~templates/web/chat.html~    .descanso { background:#FDECEC~    .descanso-sin-estilo { background:#FDECEC~$W.test_el_cartel_no_se_confunde_con_la_letra_pequena"
"La bandera del equipo se manda tambien en los recurrentes~templates/web/horarios.html~        todo_el_equipo: !recurrente && this.blo.todoElEquipo,~        todo_el_equipo: this.blo.todoElEquipo,~$W.test_el_panel_ofrece_bloquear_a_todo_el_equipo"
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
