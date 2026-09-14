#!/usr/bin/env bash
# Corrige los trece arneses de mutacion. Se corre UNA vez y es idempotente.
#
#   bash parchar_arneses.sh
#
# ── Los dos defectos que arregla ──
#
# 1. INTERPRETE FIJO. Los arneses invocan `python3` y declaran
#    `PY=.venv/bin/python`. Ninguno de los dos existe en Git Bash sobre
#    Windows, donde el binario esta en `.venv/Scripts/python.exe`. Se
#    sustituyen por una deteccion que funciona en los dos sistemas.
#
# 2. VERDE FALSO. Este es el grave. Cuando el aplicador de mutaciones
#    fallaba —por ejemplo, porque `python3` no existia— el script
#    comprobaba unicamente `if [ $? -eq 3 ]`, que es el codigo de "no
#    aplicable". Cualquier otro fallo (127 = orden no encontrada) no
#    entraba en esa rama, el bucle seguia de largo y ejecutaba las pruebas
#    contra el codigo INTACTO. El resultado era un informe que decia
#    "OK: las N comprobaciones mordieron" sin haber mutado nada.
#
#    Un arnes que informa verde cuando no verifico nada es peor que no
#    tener arnes: da confianza justo donde no la hay. Ahora cualquier
#    codigo distinto de 0 y 3 restaura los archivos y aborta con error.
set -u

DETECCION='# Interprete del entorno virtual. En Linux y macOS esta en bin/; en
# Windows (Git Bash) esta en Scripts/. Se detecta en vez de fijarse para
# que el arnes corra igual en las dos maquinas del proyecto.
if   [ -x .venv/bin/python ];         then PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
else
  echo "ERROR: no encuentro el interprete de .venv. Activa el entorno." >&2
  exit 1
fi'

parcheados=0
saltados=0

for f in mutar_*.sh; do
  [ -f "$f" ] || continue

  if grep -q "no encuentro el interprete de .venv" "$f"; then
    echo "  ya parcheado   $f"
    saltados=$((saltados+1))
    continue
  fi

  DETECCION="$DETECCION" ARCHIVO="$f" python3 - <<'PYAP'
import os, pathlib, sys

p = pathlib.Path(os.environ["ARCHIVO"])
b = p.read_bytes()
t = b.decode("utf-8")
fin = "\r\n" if b"\r\n" in b else "\n"
deteccion = os.environ["DETECCION"].replace("\n", fin)

# 1) Sustituir la declaracion fija del interprete por la deteccion.
viejo_py = "PY=.venv/bin/python"
if viejo_py not in t:
    print(f"AVISO: {p} no declara PY= como se esperaba", file=sys.stderr)
    sys.exit(2)
t = t.replace(viejo_py, deteccion, 1)

# 2) El aplicador de mutaciones usa el mismo interprete detectado.
t = t.replace('ARCHIVO="$archivo" python3 - <<', 'ARCHIVO="$archivo" $PY - <<', 1)

# 3) Abortar si el aplicador falla por cualquier motivo que no sea
#    "no aplicable". Este es el arreglo que evita el verde falso.
viejo_if = (
    '  if [ $? -eq 3 ]; then' + fin
    + '    echo ""; echo "[$i] $titulo"; echo "   !! NO APLICABLE"; '
      'nomuerden=$((nomuerden+1)); continue' + fin
    + '  fi'
)
nuevo_if = (
    '  rc=$?' + fin
    + '  if [ $rc -eq 3 ]; then' + fin
    + '    echo ""; echo "[$i] $titulo"; echo "   !! NO APLICABLE"; '
      'nomuerden=$((nomuerden+1)); continue' + fin
    + '  elif [ $rc -ne 0 ]; then' + fin
    + '    echo ""; echo "ERROR: el aplicador de mutaciones fallo '
      '(codigo $rc)." >&2' + fin
    + '    echo "Ninguna mutacion se aplico. El resultado NO es valido." >&2'
      + fin
    + '    restaurar; exit 1' + fin
    + '  fi'
)
if viejo_if not in t:
    print(f"AVISO: {p} no tiene el bloque if esperado", file=sys.stderr)
    sys.exit(2)
t = t.replace(viejo_if, nuevo_if, 1)

p.write_bytes(t.encode("utf-8"))
PYAP

  if [ $? -eq 0 ]; then
    echo "  parcheado      $f"
    parcheados=$((parcheados+1))
  else
    echo "  !! SIN PARCHEAR $f — revisar a mano" >&2
  fi
done

echo ""
echo "Parcheados: $parcheados. Ya estaban: $saltados."
echo ""
echo "Comprueba que ninguno conserve el interprete fijo:"
echo "  grep -l 'python3\\|PY=.venv/bin/python' mutar_*.sh"
echo "(no debe devolver nada)"
