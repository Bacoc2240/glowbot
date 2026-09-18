#!/usr/bin/env bash
# Arnes de mutacion del paquete de periodos de descanso. Separador: ~
#
# Antes de ejecutarlo:
#   mkdir -p /tmp/l16/agenda /tmp/l16/asistente /tmp/l16/negocios \
#            /tmp/l16/migrations /tmp/l16/templates
#   cp agenda/services.py agenda/api.py                /tmp/l16/agenda/
#   cp asistente/services.py                           /tmp/l16/asistente/
#   cp negocios/models.py negocios/api.py              /tmp/l16/negocios/
#   cp negocios/migrations/0015_bloqueo_periodo.py     /tmp/l16/migrations/
#   cp templates/web/horarios.html                     /tmp/l16/templates/
set -u
A=agenda.tests.PeriodoDeDescansoTest
I=asistente.tests.ElAsistenteNoAgendaFueraDeLaAgendaTest
W=web.tests.PantallaPeriodoDescansoTests
M=negocios.tests.MigracionPeriodoBloqueoTest
# Interprete del entorno virtual. En Linux y macOS esta en bin/; en
# Windows (Git Bash) esta en Scripts/. Se detecta en vez de fijarse para
# que el arnes corra igual en las dos maquinas del proyecto.
if   [ -x .venv/bin/python ];         then PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
else
  echo "ERROR: no encuentro el interprete de .venv. Activa el entorno." >&2
  exit 1
fi
restaurar() {
  cp /tmp/l16/agenda/services.py agenda/services.py
  cp /tmp/l16/agenda/api.py agenda/api.py
  cp /tmp/l16/asistente/services.py asistente/services.py
  cp /tmp/l16/negocios/models.py negocios/models.py
  cp /tmp/l16/negocios/api.py negocios/api.py
  cp /tmp/l16/migrations/0015_bloqueo_periodo.py negocios/migrations/0015_bloqueo_periodo.py
  cp /tmp/l16/templates/horarios.html templates/web/horarios.html
}
trap restaurar EXIT INT TERM
MUTACIONES=(
"El periodo bloquea solo su primer dia~agenda/services.py~        return (Q(recurrente=False, fecha__lte=dia, fecha_fin__gte=dia)~        return (Q(recurrente=False, fecha=dia)~$A.test_el_periodo_cierra_todos_sus_dias,$A.test_el_ultimo_dia_del_periodo_tambien_esta_cerrado"
"El ultimo dia del periodo queda abierto (rango semiabierto)~agenda/services.py~fecha__lte=dia, fecha_fin__gte=dia)~fecha__lte=dia, fecha_fin__gt=dia)~$A.test_el_ultimo_dia_del_periodo_tambien_esta_cerrado"
"El recurrente deja de aplicarse por dia de la semana~agenda/services.py~                | Q(recurrente=True, dia_semana=dia.weekday()))~                | Q(recurrente=True, dia_semana=None))~$A.test_un_bloqueo_recurrente_sigue_aplicando_por_dia_de_semana"
"save() deja de completar la fecha de fin~negocios/models.py~        if not self.recurrente and self.fecha is not None and self.fecha_fin is None:~        if False:~$A.test_un_bloqueo_de_un_dia_guarda_fecha_fin"
"La migracion no completa los bloqueos ya guardados~negocios/migrations/0015_bloqueo_periodo.py~        recurrente=False, fecha__isnull=False, fecha_fin__isnull=True,~        recurrente=True, fecha__isnull=False, fecha_fin__isnull=True,~$M.test_los_bloqueos_de_un_dia_quedan_con_su_fecha_fin,$M.test_tras_la_migracion_el_dia_bloqueado_sigue_bloqueado"
"reservar deja de mirar la jornada y los bloqueos~agenda/services.py~        if respetar_horario:~        if False:~$I.test_no_agenda_dentro_de_un_periodo_de_descanso,$I.test_no_agenda_fuera_de_la_jornada,$A.test_las_citas_fijas_saltan_los_dias_del_periodo"
"Solo se mira la hora de inicio y no donde termina la cita~agenda/services.py~        if not any(f_ini <= ini and fin <= f_fin for f_ini, f_fin in franjas):~        if not any(f_ini <= ini <= f_fin for f_ini, f_fin in franjas):~$I.test_no_agenda_una_cita_que_termina_despues_del_cierre"
"Los bloqueos dejan de mirarse al reservar~agenda/services.py~            if cls._solapan(ini, fin, b_ini, b_fin):~            if False:~$I.test_no_agenda_dentro_de_un_periodo_de_descanso,$A.test_las_citas_fijas_saltan_los_dias_del_periodo"
"El panel pierde su excepcion y no puede agendar en el descanso~agenda/api.py~                respetar_horario=False,~                respetar_horario=True,~$A.test_el_dueno_si_puede_agendar_a_mano_dentro_del_periodo"
"Al modelo se le cuenta por que no se atiende~asistente/services.py~            return None, (\"Ese profesional no atiende ese día a esa hora, \"~            return None, (\"Está bloqueado por vacaciones. \"~$I.test_al_modelo_no_se_le_cuenta_por_que_no_atiende"
"Se acepta un fin anterior al inicio~negocios/api.py~            if fin < inicio:~            if False:~$A.test_el_endpoint_rechaza_un_fin_anterior_al_inicio"
"Se acepta un periodo de varios dias con franja de horas~negocios/api.py~            if fin > inicio and data.get(\"hora_inicio\") is not None:~            if False:~$A.test_el_endpoint_rechaza_un_periodo_con_horas"
"El tope de dias desaparece~negocios/api.py~            if dias > DIAS_MAX_PERIODO_BLOQUEO:~            if False:~$A.test_el_endpoint_rechaza_un_periodo_desmedido"
"Se aceptan periodos que ya terminaron~negocios/api.py~            if fin < timezone.localdate():~            if False:~$A.test_el_endpoint_rechaza_un_periodo_que_ya_termino"
"Un periodo en curso se rechaza por haber empezado ayer~negocios/api.py~            if fin < timezone.localdate():~            if inicio < timezone.localdate():~$A.test_el_endpoint_acepta_un_periodo_en_curso"
"El recurrente con fecha_fin se acepta en silencio~negocios/api.py~        if data.get(\"recurrente\") and data.get(\"fecha_fin\") is not None:~        if False:~$A.test_el_endpoint_rechaza_un_recurrente_con_fecha_fin"
"Un bloqueo sin fecha_fin deja de completarse (se rompe la compatibilidad)~negocios/api.py~            fin = data.get(\"fecha_fin\") or inicio~            fin = data.get(\"fecha_fin\")~$A.test_sin_fecha_fin_se_sigue_creando_un_bloqueo_de_un_dia"
"El aviso de citas afectadas se calla~negocios/api.py~        } for c in AgendaService.citas_bajo_bloqueo(bloqueo)]~        } for c in []]~$A.test_el_endpoint_devuelve_las_citas_que_quedan_dentro"
"El aviso incluye citas que el bloqueo no toca~agenda/services.py~            if cubre[c.fecha] and cls._solapan(~            if cubre[c.fecha] or cls._solapan(~$A.test_un_bloqueo_por_horas_solo_alcanza_a_las_citas_que_solapa"
"El aviso cuenta citas ya canceladas~agenda/services.py~            profesional=bloqueo.profesional, estado=Cita.Estado.CONFIRMADA,~            profesional=bloqueo.profesional,~$A.test_una_cita_cancelada_no_figura_como_afectada"
"La pantalla no manda la fecha de fin~templates/web/horarios.html~        fecha_fin: periodo ? this.blo.fecha_fin : null,~        fecha_fin: null,~$W.test_el_periodo_viaja_con_fecha_fin_al_guardar"
"El bloqueo rechazado se vuelve a callar~templates/web/horarios.html~x-text=\"errorBloqueo\"~x-text=\"\"~$W.test_el_bloqueo_rechazado_se_dice"
"Las citas que quedan dentro dejan de mostrarse~templates/web/horarios.html~x-show=\"citasAfectadas.length\"~x-show=\"false\"~$W.test_las_citas_que_quedan_dentro_se_muestran"
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
