#!/usr/bin/env bash
# Arnes de mutacion del fijado de versiones.
set -u
W=web.tests.DependenciasFijadasTests
PY=.venv/bin/python
restaurar() { cp /tmp/limpio3/requirements.txt requirements.txt; }
trap restaurar EXIT INT TERM

MUTACIONES=(
"Una dependencia se anade sin version|qrcode==8.2|qrcode|$W.test_ninguna_dependencia_queda_sin_version"
"Se vuelve a fijar una serie con comodin|Django==4.2.30|Django==4.2.*|$W.test_no_se_fija_una_serie_en_lugar_de_una_version"
"Se retira una dependencia que el codigo importa|Pillow==12.3.0|# Pillow retirada|$W.test_las_dependencias_que_el_codigo_importa_estan_declaradas"
)

total=0; nomuerden=0
for m in "${MUTACIONES[@]}"; do
  IFS='|' read -r titulo viejo nuevo prueba <<< "$m"
  restaurar
  VIEJO="$viejo" NUEVO="$nuevo" python3 -c "
import os, sys, pathlib
p = pathlib.Path('requirements.txt'); t = p.read_text(encoding='utf-8')
if os.environ['VIEJO'] not in t: print('NO_APLICABLE'); sys.exit(3)
p.write_text(t.replace(os.environ['VIEJO'], os.environ['NUEVO'], 1), encoding='utf-8')"
  if [ $? -eq 3 ]; then echo ""; echo "[$titulo]"; echo "   !! NO APLICABLE"; nomuerden=$((nomuerden+1)); continue; fi
  echo ""; echo "[$titulo]"
  total=$((total+1))
  if timeout 60 $PY manage.py test -v 0 --keepdb "$prueba" >/dev/null 2>&1; then
    echo "   NO MUERDE  ${prueba##*.}"; nomuerden=$((nomuerden+1))
  else
    echo "   MUERDE     ${prueba##*.}"
  fi
done
restaurar
echo ""
echo "===================================================================="
if [ $nomuerden -gt 0 ]; then echo "FALLO: $nomuerden de $total no mordieron"; exit 1; fi
echo "OK: las $total comprobaciones mordieron."
