#!/usr/bin/env bash
# Arnes de mutacion del paquete "no negar sin consultar, y la sesion caduca".
#
# Reintroduce cada defecto uno por uno y comprueba que la prueba que lo
# cubre FALLA. Una prueba que sigue pasando con el codigo roto no prueba nada.
#
# El separador de campos es ~ y no |, porque las mutaciones de este paquete
# contienen expresiones regulares llenas de | y con el separador antiguo el
# script partia mal las lineas y daba veredictos sin sentido.
#
# Antes de ejecutarlo hay que guardar el original:
#   mkdir -p /tmp/l10/asistente
#   cp asistente/services.py /tmp/l10/asistente/
set -u
N=asistente.tests.NoNegarSinConsultarTest
C=asistente.tests.ConversacionCaducaTest
R=asistente.tests.ResumenDeEstadoTest
A=asistente.tests.CuandoLaApiFallaTest
M=asistente.tests.MarcaDeActividadTest
PY=.venv/bin/python
restaurar() { cp /tmp/l10/asistente/services.py asistente/services.py; }
trap restaurar EXIT INT TERM

MUTACIONES=(
"La red se desarma: cualquier negativa llega al cliente~asistente/services.py~                if el_backend_hablo or not cls.niega_disponibilidad(texto):~                if True:~$N.test_una_negativa_sin_consultar_no_llega_al_cliente,$N.test_si_el_modelo_insiste_no_se_le_entrega_la_negativa"
"La red se arma siempre: tampoco deja pasar la negativa del backend~asistente/services.py~                if el_backend_hablo or not cls.niega_disponibilidad(texto):~                if not cls.niega_disponibilidad(texto):~$N.test_si_el_sistema_dice_que_no_hay_el_modelo_si_puede_decirlo"
"El detector deja de reconocer el caso real (horario ... ya paso)~asistente/services.py~    _AGENDA + r\"[^.!?]{0,25}\\bya\\s+pas[óo]\\b\",~~$N.test_reconoce_las_formas_de_decir_que_no_hay,$N.test_una_negativa_sin_consultar_no_llega_al_cliente"
"El detector salta con cualquier negacion~asistente/services.py~_RE_NIEGA = re.compile(\"|\".join(_NEGACIONES), re.IGNORECASE)~_RE_NIEGA = re.compile(r\"\\bno\\b\", re.IGNORECASE)~$N.test_no_confunde_otras_negativas_con_falta_de_agenda"
"La regla 17 desaparece del prompt~asistente/services.py~17. NUNCA digas que no hay disponibilidad~17. Conviene revisar la disponibilidad~$N.test_el_prompt_lleva_la_regla_escrita"
"La marca vuelve a hablar del reloj en vez de la cita~asistente/services.py~            marca = \" — HISTORIAL (ya se atendio, no ocupa cupo)\" if ya_paso else \"\"~            marca = \" — YA PASO\" if ya_paso else \"\"~$R.test_la_de_esta_manana_aparece_marcada"
"La conversacion no caduca nunca (el defecto original)~asistente/services.py~        if conv is not None and (timezone.now() - conv.actualizado_en\\n                                 <= CADUCIDAD_CONVERSACION):~        if conv is not None:~$C.test_pasada_la_ventana_se_empieza_de_cero,$C.test_el_telefono_del_anterior_no_se_le_inyecta_al_siguiente"
"La conversacion caduca en cada mensaje~asistente/services.py~        if conv is not None and (timezone.now() - conv.actualizado_en\\n                                 <= CADUCIDAD_CONVERSACION):~        if False:~$C.test_dentro_de_la_ventana_se_continua_la_misma"
"La ventana de caducidad se pone a cero~asistente/services.py~CADUCIDAD_CONVERSACION = timedelta(hours=12)~CADUCIDAD_CONVERSACION = timedelta(hours=0)~$C.test_dentro_de_la_ventana_se_continua_la_misma"
"Al caducar se reescribe la fila vieja en vez de abrir otra~asistente/services.py~        return ConversacionIA.objects.create(\\n            establecimiento=establecimiento, session_id=session_id)~        conv.mensajes = []; conv.telefono_cliente = \"\"; conv.save(); return conv~$C.test_la_conversacion_vieja_se_conserva_intacta"
"Un fallo de la API vuelve a salir como error 500~asistente/services.py~            except Exception as exc:~            except ValueError as exc:~$A.test_una_sobrecarga_no_llega_como_error_al_cliente,$A.test_el_endpoint_responde_200_y_no_500"
"Un defecto propio se confunde con una sobrecarga del proveedor~asistente/services.py~                if cls._es_error_del_proveedor(exc):~                if True:~$A.test_un_defecto_nuestro_tampoco_sale_en_crudo_pero_se_registra"
"Una sobrecarga se confunde con un defecto propio~asistente/services.py~                if cls._es_error_del_proveedor(exc):~                if False:~$A.test_una_sobrecarga_no_llega_como_error_al_cliente"
"El turno fallido se persiste a medias~asistente/services.py~        conv.save(update_fields=[\"tokens_entrada\", \"tokens_salida\",\n                                 \"actualizado_en\"])\n        return {\"respuesta\": respuesta, \"accion\": accion, \"cita\": None}~        conv.mensajes = [{\"role\": \"user\", \"content\": \"roto\"}]\n        conv.save()\n        return {\"respuesta\": respuesta, \"accion\": accion, \"cita\": None}~$A.test_el_turno_fallido_no_se_persiste"
"Los tokens gastados en un turno fallido se pierden~asistente/services.py~        conv.save(update_fields=[\"tokens_entrada\", \"tokens_salida\",\n                                 \"actualizado_en\"])~        pass~$A.test_los_tokens_gastados_si_quedan_registrados,$M.test_tambien_cuando_el_turno_falla"
"El presupuesto de turno deja de cortar~asistente/services.py~            if iteracion and time.monotonic() - inicio > PRESUPUESTO_TURNO:~            if False:~$A.test_el_presupuesto_corta_antes_de_quemar_el_worker"
"El presupuesto corta antes del primer intento~asistente/services.py~            if iteracion and time.monotonic() - inicio > PRESUPUESTO_TURNO:~            if time.monotonic() - inicio > PRESUPUESTO_TURNO:~$A.test_siempre_se_intenta_al_menos_una_vez"
"El cliente del SDK pierde sus limites~asistente/services.py~            timeout=TIMEOUT_API, max_retries=REINTENTOS_API,~~$A.test_el_cliente_del_sdk_lleva_limites_explicitos"
"La marca de actividad deja de escribirse (el defecto que se escapo)~asistente/services.py~                                 \"telefono_cliente\", \"actualizado_en\"])~                                 \"telefono_cliente\"])~$M.test_cada_mensaje_refresca_la_marca"
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
