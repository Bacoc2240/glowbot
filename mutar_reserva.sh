#!/usr/bin/env bash
# Arnes de mutacion del paquete de reserva manual.
# La restauracion se hace copiando desde una carpeta limpia y no deshaciendo
# la sustitucion, para que una interrupcion no deje codigo mutado en el arbol.
# Uso:  bash mutar_reserva.sh [inicio] [fin]
set -u
LIMPIO=/tmp/limpio2
A=agenda.tests.ReservaManualTest
W=web.tests.PantallaReservaManualTests
PY=.venv/bin/python

restaurar() {
  cp "$LIMPIO/agenda/api.py"            agenda/api.py
  cp "$LIMPIO/agenda/services.py"       agenda/services.py
  cp "$LIMPIO/templates/web/panel.html" templates/web/panel.html
}
trap restaurar EXIT INT TERM

MUTACIONES=(
"Las colas de las relaciones vuelven a ser de todos los inquilinos|agenda/api.py|                self.fields[campo].queryset = (\\n                    modelo.objects.del_establecimiento(establecimiento))|                self.fields[campo].queryset = modelo.objects.all()|$A.test_no_se_puede_agendar_con_el_profesional_de_otro_negocio,$A.test_no_se_puede_agendar_a_un_cliente_de_otro_negocio,$A.test_no_se_puede_agendar_con_el_servicio_de_otro_negocio"
"El contexto deja de inyectarse y las colas quedan vacias|agenda/api.py|        contexto[\"establecimiento\"] = self.request.user.establecimientos.first()|        pass|$A.test_la_cita_manual_queda_marcada_como_manual"
"La cita manual se marca con el canal del asistente|agenda/api.py|                canal=Cita.Canal.MANUAL,|                canal=Cita.Canal.IA,|$A.test_la_cita_manual_queda_marcada_como_manual"
"El bloqueo se salta en silencio, sin avisar al dueno|agenda/api.py|                respetar_bloqueo=not confirmado,|                respetar_bloqueo=False,|$A.test_un_telefono_bloqueado_avisa_antes_de_agendar"
"El bloqueo vuelve a impedir la reserva manual pese a confirmarla|agenda/api.py|                respetar_bloqueo=not confirmado,|                respetar_bloqueo=True,|$A.test_confirmando_el_aviso_el_duenio_puede_agendar_igual"
"El tope del autoservicio vuelve a frenar al dueno|agenda/api.py|                respetar_tope=False,|                respetar_tope=True,|$A.test_el_tope_de_citas_no_frena_al_duenio"
"Levantar el tope en el panel lo levanta tambien en el chat publico|agenda/services.py|        if respetar_tope and abiertas >= tope:|        if False and abiertas >= tope:|$A.test_el_tope_sigue_frenando_al_autoservicio"
"Levantar el bloqueo en el panel lo levanta tambien en el chat publico|agenda/services.py|        if respetar_bloqueo and TelefonoBloqueado.objects.filter(|        if False and TelefonoBloqueado.objects.filter(|$A.test_el_bloqueo_sigue_frenando_al_autoservicio"
"El boton de agendar queda desconectado (la funcion sigue definida)|templates/web/panel.html|@click=\"reservar(false)\"|@nada=\"\"|$W.test_la_agenda_ofrece_agendar_y_el_boton_esta_conectado"
"El aviso de bloqueo reenvia sin confirmar, saltandose el veto|templates/web/panel.html|@click=\"reservar(true)\"|@click=\"reservar(false)\"|$W.test_el_aviso_de_bloqueo_exige_una_segunda_pulsacion"
"La pantalla crea el cliente por su cuenta, saltandose el consentimiento|templates/web/panel.html|<a href=\"/panel/clientes\">Regístralo primero</a>|<button @click='api(\"/clientes\", { method: \"POST\" })'>Crear</button>|$W.test_si_el_cliente_no_existe_manda_a_registrarlo_y_no_lo_crea_aqui"
)

desde=${1:-0}; hasta=${2:-${#MUTACIONES[@]}}
total=0; nomuerden=0
for ((i=desde; i<hasta && i<${#MUTACIONES[@]}; i++)); do
  IFS='|' read -r titulo archivo viejo nuevo pruebas <<< "${MUTACIONES[$i]}"
  restaurar
  VIEJO="$viejo" NUEVO="$nuevo" ARCHIVO="$archivo" python3 - <<'PYAP'
import os, sys, pathlib
p = pathlib.Path(os.environ["ARCHIVO"])
t = p.read_bytes().decode("utf-8")
# El terminador se toma del propio archivo: api.py y panel.html usan CRLF y
# una mutacion multilinea escrita con \n daria un falso "no aplicable".
fin = "\r\n" if b"\r\n" in p.read_bytes() else "\n"
viejo = os.environ["VIEJO"].replace("\\n", fin)
nuevo = os.environ["NUEVO"].replace("\\n", fin)
if viejo not in t:
    print("NO_APLICABLE"); sys.exit(3)
p.write_bytes(t.replace(viejo, nuevo, 1).encode("utf-8"))
PYAP
  if [ $? -eq 3 ]; then
    echo ""; echo "[$i] $titulo"; echo "   !! MUTACION NO APLICABLE"; nomuerden=$((nomuerden+1)); continue
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
