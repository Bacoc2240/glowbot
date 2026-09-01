#!/usr/bin/env bash
# Arnes de mutacion del paquete de jornada partida y formato de 12 horas.
# La restauracion copia desde una carpeta limpia, no deshace la sustitucion,
# para que una interrupcion no deje codigo mutado en el arbol.
set -u
A=agenda.tests
W=web.tests
PY=.venv/bin/python
restaurar() {
  cp /tmp/l4/agenda/fechas.py agenda/fechas.py
  cp /tmp/l4/agenda/api.py agenda/api.py
  cp /tmp/l4/negocios/api.py negocios/api.py
  cp /tmp/l4/templates/web/horarios.html templates/web/horarios.html
  cp /tmp/l4/templates/web/panel.html templates/web/panel.html
}
trap restaurar EXIT INT TERM

MUTACIONES=(
"El mediodia vuelve a ser p. m.|agenda/fechas.py|    if h.hour == 12 and h.minute == 0:\\n        return \"12:00 m.\"|    if False:\\n        return \"12:00 m.\"|$A.FormatoDeHoraTest.test_el_mediodia_no_es_ni_am_ni_pm"
"La medianoche sale como cero|agenda/fechas.py|    if h.hour == 0:\\n        return f\"12:{minutos} a. m.\"|    if False:\\n        return f\"12:{minutos} a. m.\"|$A.FormatoDeHoraTest.test_la_medianoche_no_sale_como_cero"
"Se pierde el modulo 12 y la tarde sale en 24 horas|agenda/fechas.py|    return f\"{h.hour % 12}:{minutos} {sufijo}\"|    return f\"{h.hour}:{minutos} {sufijo}\"|$A.FormatoDeHoraTest.test_la_manana_y_la_tarde_se_distinguen"
"Se abandona la abreviatura de la RAE|agenda/fechas.py|    sufijo = \"a. m.\" if h.hour < 12 else \"p. m.\"|    sufijo = \"am\" if h.hour < 12 else \"pm\"|$A.FormatoDeHoraTest.test_se_usa_la_abreviatura_de_la_rae"
"El endpoint deja de mandar la etiqueta legible|agenda/api.py|{\"valor\": s.strftime(\"%H:%M\"), \"texto\": hora_texto(s)}|{\"valor\": s.strftime(\"%H:%M\"), \"texto\": s.strftime(\"%H:%M\")}|$A.JornadaPartidaTest.test_las_horas_libres_llegan_con_su_etiqueta_legible"
"Guardar la tarde vuelve a borrar la manana|negocios/api.py|        exc = ExcepcionHorario.objects.create(\\n            profesional=prof, **s.validated_data)|        exc, _ = ExcepcionHorario.objects.update_or_create(\\n            profesional=prof, fecha=s.validated_data[\"fecha\"],\\n            defaults={\"hora_inicio\": s.validated_data[\"hora_inicio\"],\\n                      \"hora_fin\": s.validated_data[\"hora_fin\"]})|$W.HorariosFlexiblesTest.test_una_fecha_admite_dos_jornadas"
"La pantalla vuelve a leer solo la primera franja del dia|templates/web/horarios.html|          const delDia = franjas\\n            .filter(x => x.dia_semana === i)|          const delDia = [franjas.find(x => x.dia_semana === i)].filter(Boolean)\\n            .filter(x => true)|$W.PantallaJornadaPartidaTests.test_la_pantalla_lee_todas_las_franjas_del_dia"
"Se deja de enviar la segunda jornada al guardar|templates/web/horarios.html|        if (d.partida) {\\n          franjas.push({ dia_semana: i, hora_inicio: d.inicio2, hora_fin: d.fin2 });\\n        }|        if (false) {\\n          franjas.push({ dia_semana: i, hora_inicio: d.inicio2, hora_fin: d.fin2 });\\n        }|$W.PantallaJornadaPartidaTests.test_al_guardar_se_envia_tambien_la_segunda_jornada"
"Desaparece el eco de la hora legible bajo los selectores|templates/web/horarios.html|               x-text=\"textoFranja(d.inicio, d.fin)\"|               x-nada=\"\"|$W.PantallaJornadaPartidaTests.test_cada_hora_lleva_debajo_su_lectura_en_doce_horas"
"El listado vuelve a la fecha ISO y la hora de 24|templates/web/horarios.html|x-text=\"e.fecha_texto + ' · ' + e.franja_texto\"|x-text=\"e.fecha + ' · ' + e.hora_inicio.slice(0,5)\"|$W.PantallaJornadaPartidaTests.test_los_listados_usan_las_etiquetas_del_backend"
"Se pierde el boton de copiar a los demas dias|templates/web/horarios.html|@click=\"copiarALosDemas()\"|@nada=\"\"|$W.PantallaJornadaPartidaTests.test_se_puede_copiar_el_horario_a_los_demas_dias"
"La tarde puede solaparse con la manana sin aviso|templates/web/horarios.html|      if (d.inicio2 < d.fin) return \"La tarde no puede empezar antes de que cierre la mañana.\";|      if (false) return \"\";|$W.PantallaJornadaPartidaTests.test_la_tarde_no_puede_empezar_antes_de_cerrar_la_manana"
"La agenda vuelve a mostrar la hora en 24 horas|templates/web/panel.html|x-text=\"c.hora_texto\"|x-text=\"c.hora_inicio.slice(0,5)\"|$W.PantallaHoraLegibleTests.test_la_agenda_muestra_la_hora_escrita_por_el_backend"
)

desde=${1:-0}; hasta=${2:-${#MUTACIONES[@]}}
total=0; nomuerden=0
for ((i=desde; i<hasta && i<${#MUTACIONES[@]}; i++)); do
  IFS='|' read -r titulo archivo viejo nuevo pruebas <<< "${MUTACIONES[$i]}"
  restaurar
  VIEJO="$viejo" NUEVO="$nuevo" ARCHIVO="$archivo" python3 - <<'PYAP'
import os, sys, pathlib
p = pathlib.Path(os.environ["ARCHIVO"])
b = p.read_bytes(); t = b.decode("utf-8")
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
