#!/usr/bin/env bash
# Arnes de mutacion de la pantalla guiada. Separador: ~
#
# Antes de ejecutarlo:
#   mkdir -p /tmp/l20/templates
#   cp templates/web/chat.html /tmp/l20/templates/
#
# Cuidado al escribir mutaciones sobre plantillas: las entradas van entre
# comillas dobles, asi que bash expande ${...} y ejecuta lo que este entre
# acentos graves. Una mutacion asi se aplica vacia y la prueba pasa: parece
# que la comprobacion no muerde cuando lo que fallo fue el arnes. Se eligen
# fragmentos sin $ ni acentos graves.
#
# Limite conocido, y conviene decirlo: estas pruebas no ejecutan JavaScript.
# Comprueban que el enganche esta escrito, no que funcione. Lo que protege de
# verdad este camino son las pruebas de los dos endpoints.
set -u
W=web.tests.PantallaGuiadaTests
F=web.tests.FranjaConsentimientoTests
if   [ -x .venv/bin/python ];         then PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
else
  echo "ERROR: no encuentro el interprete de .venv. Activa el entorno." >&2
  exit 1
fi
restaurar() { cp /tmp/l20/templates/chat.html templates/web/chat.html; }
trap restaurar EXIT INT TERM
MUTACIONES=(
"La pantalla arranca saltandose la autorizacion~templates/web/chat.html~    paso: \"consentimiento\", errorPaso~    paso: \"agenda\", errorPaso~$W.test_el_primer_paso_es_la_autorizacion"
"Se avanza aunque el servidor no deje constancia~templates/web/chat.html~        this.paso = \"identidad\";~        this.paso = \"agenda\";~$W.test_la_identidad_va_antes_de_elegir_la_hora"
"El aviso deja de decir que se guardan los datos~templates/web/chat.html~ necesita guardar tu~ atiende con cita previa y~$F.test_la_franja_anuncia_y_no_presupone"
"Los datos del cliente dejan de recordarse~templates/web/chat.html~      localStorage.setItem(\"glowbot_cliente_\" + this.slug,~      localStorage.removeItem(\"glowbot_cliente_\" + this.slug); void (~$W.test_los_datos_se_recuerdan_en_el_navegador"
"Las horas se le piden al modelo en vez de al endpoint~templates/web/chat.html~slug}/disponibilidad~slug}/chat~$W.test_las_horas_se_piden_al_endpoint_sin_tokens"
"La tira de dias deja de decir si hay cupo~templates/web/chat.html~x-text=\"d.libres ? 'Cupos disponibles' : 'Sin cupo'\"~x-text=\"''\"~$W.test_la_tira_de_dias_dice_si_hay_cupo"
"La tira vuelve a cantar el numero de horas~templates/web/chat.html~x-text=\"d.libres ? 'Cupos disponibles' : 'Sin cupo'\"~x-text=\"d.libres ? d.libres + ' horas' : 'Sin cupo'\"~$W.test_la_tira_de_dias_dice_si_hay_cupo"
"La entrada al asistente pierde el dorado de la marca~templates/web/chat.html~    <div class=\"tarjeta asistente\" x-show=\"!fallo\">~    <div class=\"tarjeta\" x-show=\"!fallo\">~$W.test_la_entrada_al_asistente_se_ve"
"Los dias sin cupo se pueden pulsar igual~templates/web/chat.html~                    :disabled=\"!d.libres\" @click=\"diaSel = d.fecha\"~                    @click=\"diaSel = d.fecha\"~$W.test_la_tira_de_dias_dice_si_hay_cupo"
"La pantalla vuelve a abrir en hoy y no en el primer dia con cupo~templates/web/chat.html~          this.diaSel = d.primer_dia_con_cupo;~          this.diaSel = d.desde;~$W.test_la_pantalla_abre_en_el_primer_dia_con_cupo"
"El selector permite elegir un dia que ya paso~templates/web/chat.html~          <input id=\"otra-fecha\" type=\"date\" x-model=\"desde\" :min=\"hoy\"~          <input id=\"otra-fecha\" type=\"date\" x-model=\"desde\"~$W.test_no_se_puede_pedir_un_dia_pasado_desde_la_pantalla"
"A quien no tiene horas se le esconde~templates/web/chat.html~            <div class=\"vacio\" x-show=\"!p.horas.length\">Sin horas libres ese día</div>~            <div class=\"vacio\" x-show=\"false\"></div>~$W.test_a_quien_no_tiene_horas_se_le_pinta_apagado"
"Se pregunta por el profesional aunque solo haya uno~templates/web/chat.html~        <div class=\"fila\" x-show=\"profesionales.length > 1\"~        <div class=\"fila\" x-show=\"true\"~$W.test_el_filtro_de_profesional_solo_sale_si_hay_a_quien_elegir"
"La hora pulsada ya no crea la cita~templates/web/chat.html~                <button type=\"button\" class=\"opcion\" @click=\"agendar(p, h)\"~                <button type=\"button\" class=\"opcion\"~$W.test_la_hora_pulsada_crea_la_cita"
"El hueco perdido no recarga la agenda~templates/web/chat.html~        } else if (r.status === 409) {~        } else if (false) {~$W.test_el_hueco_perdido_recarga_la_agenda"
"La confirmacion pierde los enlaces de calendario~templates/web/chat.html~        <a class=\"accion\" :href=\"cita ? cita.google : '#'\" target=\"_blank\"~        <a class=\"accion\" href=\"#\" target=\"_blank\"~$W.test_la_confirmacion_trae_los_enlaces_de_calendario"
"El asistente se ofrece antes de aceptar~templates/web/chat.html~    <div class=\"tarjeta asistente\" x-show=\"!fallo && paso !== 'consentimiento'\">~    <div class=\"tarjeta asistente\" x-show=\"!fallo\">~$W.test_el_asistente_no_se_ofrece_antes_de_aceptar"
"El chat pide autorizacion sin pintar el boton~templates/web/chat.html~        <div class=\"acciones\" x-show=\"m.pedirConsentimiento && !consentido\">~        <div class=\"acciones\" x-show=\"false\">~$W.test_el_chat_pinta_su_propio_boton_de_aceptar"
"La respuesta del chat deja de traer la peticion de consentimiento~templates/web/chat.html~            pedirConsentimiento: d.accion === \"pedir_consentimiento\" });~            });~$W.test_el_chat_pinta_su_propio_boton_de_aceptar"
"La autorizacion del camino guiado no vale en el chat~templates/web/chat.html~        this.paso = \"identidad\";\n        // Vale para las dos puertas: si luego pasa al chat, el asistente no\n        // tiene por que volver a pedir lo que ya esta registrado.\n        this.consentido = true;~        this.paso = \"identidad\";~$W.test_la_autorizacion_del_camino_guiado_vale_en_el_chat"
"Volver pierde el color de la marca~templates/web/chat.html~      <button type=\"button\" class=\"accion dorada\" @click=\"escribiendo = false\">~      <button type=\"button\" class=\"accion secundaria\" @click=\"escribiendo = false\">~$W.test_los_botones_de_salida_llevan_el_color_de_la_marca"
"Agendar otra cita pierde el color de la marca~templates/web/chat.html~        <button type=\"button\" class=\"accion dorada\" @click=\"otraCita()\">~        <button type=\"button\" class=\"accion secundaria\" @click=\"otraCita()\">~$W.test_los_botones_de_salida_llevan_el_color_de_la_marca"
"Desaparece la salida hacia el asistente~templates/web/chat.html~        Hablar con el asistente ✨</button>~        </button>~$W.test_el_asistente_sigue_disponible_como_respaldo"
"Empezar de nuevo deja los datos de la persona anterior~templates/web/chat.html~      localStorage.removeItem(\"glowbot_cliente_\" + this.slug);\n      this.sessionId = null;~      this.sessionId = null;~$W.test_empezar_de_nuevo_borra_tambien_los_datos_del_cliente"
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
