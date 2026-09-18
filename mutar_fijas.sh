#!/usr/bin/env bash
# Arnes de mutacion del paquete de citas fijas semanales. Separador: ~
#
# Antes de ejecutarlo:
#   mkdir -p /tmp/l13/agenda
#   cp agenda/services.py agenda/api.py /tmp/l13/agenda/
#
# La mutacion de la jornada apunta a `if respetar_horario:` dentro de
# `reservar`. Antes apuntaba a la llamada que las citas fijas hacian por su
# cuenta justo antes de reservar; esa comprobacion se mudo a `reservar` con
# el paquete de periodos de descanso, para que el asistente tampoco pudiera
# saltarsela, y mantener las dos habria sido tener dos definiciones de "ese
# dia se atiende".
set -u
F=agenda.tests.CitasFijasSemanalesTest
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
  cp /tmp/l13/agenda/services.py agenda/services.py
  cp /tmp/l13/agenda/api.py agenda/api.py
}
trap restaurar EXIT INT TERM
MUTACIONES=(
"La tanda falla entera si una fecha no cabe~agenda/services.py~            except (SlotNoDisponible, DiaNoAtendido, CitaEnElPasado,\n                    TelefonoVetado) as e:~            except ZeroDivisionError as e:~$F.test_un_hueco_ocupado_se_salta_y_se_informa,$F.test_un_dia_bloqueado_se_salta"
"Las fechas saltadas se callan~agenda/services.py~                saltadas.append({\"fecha\": dia, \"motivo\": str(e)})~                pass~$F.test_un_hueco_ocupado_se_salta_y_se_informa,$F.test_el_motivo_del_salto_llega_escrito"
"El tope vuelve a frenar la tanda~agenda/services.py~                    respetar_tope=False,~                    respetar_tope=True,~$F.test_el_tope_no_frena_la_tanda"
"Deja de validarse la jornada y planta citas fuera de horario~agenda/services.py~        if respetar_horario:~        if False:~$F.test_no_planta_citas_fuera_de_la_jornada,$F.test_un_dia_bloqueado_se_salta"
"Los bloqueos dejan de mirarse~agenda/services.py~            if cls._solapan(ini, fin, b_ini, b_fin):~            if False:~$F.test_un_dia_bloqueado_se_salta"
"La cita original se queda fuera de la tanda~agenda/services.py~        if cita.serie is None:\n            cita.serie = serie\n            cita.save(update_fields=[\"serie\"])~        if False:\n            pass~$F.test_la_original_entra_en_la_tanda,$F.test_cancelar_la_serie_se_lleva_las_futuras"
"Repetir dos veces parte el grupo en dos~agenda/services.py~        serie = cita.serie or uuid.uuid4()~        serie = uuid.uuid4()~$F.test_repetir_dos_veces_no_parte_el_grupo_en_dos"
"Cancelar la tanda arrasa tambien con lo ya atendido~agenda/services.py~        return cls.solo_futuras(Cita.objects.filter(\n            establecimiento=establecimiento, serie=serie,\n            estado=Cita.Estado.CONFIRMADA,\n        )).update~        return (Cita.objects.filter(\n            establecimiento=establecimiento, serie=serie,\n            estado=Cita.Estado.CONFIRMADA,\n        )).update~$F.test_cancelar_la_serie_no_toca_lo_ya_atendido"
"Cancelar una tanda se lleva las de otra~agenda/services.py~            establecimiento=establecimiento, serie=serie,\n            estado=Cita.Estado.CONFIRMADA,\n        )).update~            establecimiento=establecimiento,\n            estado=Cita.Estado.CONFIRMADA,\n        )).update~$F.test_cancelar_una_serie_no_toca_otra"
"El limite de semanas desaparece~agenda/services.py~        if not 1 <= semanas <= cls.SEMANAS_MAX:~        if False:~$F.test_semanas_fuera_de_rango_se_rechazan,$F.test_semanas_invalidas_dan_400"
"El endpoint deja de informar de lo saltado~agenda/api.py~for x in parte[\"saltadas\"]],~for x in []],~$F.test_el_endpoint_nombra_las_fechas_saltadas"
"Una cita suelta acepta cancelar-serie~agenda/api.py~        if cita.serie is None:~        if False:~$F.test_una_cita_suelta_no_tiene_serie_que_cancelar"
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
