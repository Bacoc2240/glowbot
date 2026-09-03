#!/usr/bin/env bash
# Arnes de mutacion del paquete de consentimiento. Separador: ~
#
# Antes de ejecutarlo:
#   mkdir -p /tmp/l12/asistente
#   cp asistente/services.py asistente/api.py /tmp/l12/asistente/
set -u
K=asistente.tests.ConsentimientoLoDisponeElBackendTest
L=asistente.tests.ElModeloVeElConsentimientoTest
PY=.venv/bin/python
restaurar() {
  cp /tmp/l12/asistente/services.py asistente/services.py
  cp /tmp/l12/asistente/api.py asistente/api.py
}
trap restaurar EXIT INT TERM
MUTACIONES=(
"La puerta se abre sin constancia (el defecto original)~asistente/services.py~                if conv is None or conv.consentimiento_en is None:~                if False:~$K.test_sin_constancia_no_hay_cita,$K.test_la_ia_ya_no_puede_concederlo_desde_su_json,$K.test_la_constancia_es_de_una_sola_conversacion"
"La puerta se cierra siempre: nadie puede agendar~asistente/services.py~                if conv is None or conv.consentimiento_en is None:~                if True:~$K.test_con_constancia_la_cita_se_crea"
"Se guarda la version vigente y no la que vio el titular~asistente/services.py~                    version=conv.version_aviso or None,~                    version=None,~$K.test_se_guarda_la_version_que_el_titular_vio"
"La conversacion deja de llegar al ejecutor~asistente/services.py~                establecimiento, intencion, conv=conv)~                establecimiento, intencion)~$K.test_el_flujo_completo_por_el_chat_crea_la_cita"
"La peticion deja de ser una accion reconocible~asistente/services.py~                    \"accion\": \"pedir_consentimiento\",~                    \"accion\": None,~$K.test_el_texto_de_la_peticion_lo_escribe_el_backend,$K.test_el_texto_del_modelo_se_descarta_en_ese_turno"
"La regla 5 desaparece del prompt~asistente/services.py~   boton vale como aceptacion.~   boton es una opcion mas.~$K.test_el_prompt_le_prohibe_interpretar_la_aceptacion"
"El endpoint no exige sesion~asistente/api.py~        if conv is None:~        if False:~$K.test_sin_sesion_no_se_registra_nada"
"El endpoint no registra nada~asistente/api.py~            conv.consentimiento_en = timezone.now()~            pass~$K.test_el_boton_deja_instante_y_version"
"Cada pulsacion reescribe el instante original~asistente/api.py~        if conv.consentimiento_en is None:~        if True:~$K.test_pulsar_dos_veces_no_reescribe_la_primera_vez"
"El modelo deja de ver el consentimiento (el fallo de campo)~asistente/services.py~            \"content\": \"[SISTEMA] \" + cls._estado_consentimiento(conv),~            \"content\": \"[SISTEMA] hola\",~$L.test_cuando_esta_registrado_el_modelo_lo_ve,$L.test_pulsar_cambia_lo_que_el_modelo_lee"
"La linea dice siempre lo mismo, pulse o no~asistente/services.py~        if conv is not None and conv.consentimiento_en is not None:~        if False:~$L.test_cuando_esta_registrado_el_modelo_lo_ve,$L.test_pulsar_cambia_lo_que_el_modelo_lee"
"La linea se persiste y queda una version vieja~asistente/services.py~                (f\"[SISTEMA] {MARCA_ESTADO}\", f\"[SISTEMA] {MARCA_CONSENTIMIENTO}\"))~                f\"[SISTEMA] {MARCA_ESTADO}\")~$L.test_la_linea_no_se_persiste_en_el_historial"
"La linea vuelve a depender de que haya telefono~asistente/services.py~        historial.append({\n            \"role\": \"user\",\n            \"content\": \"[SISTEMA] \" + cls._estado_consentimiento(conv),\n        })~        if conv.telefono_cliente:\n            historial.append({\n                \"role\": \"user\",\n                \"content\": \"[SISTEMA] \" + cls._estado_consentimiento(conv),\n            })~$L.test_la_linea_llega_antes_de_que_haya_telefono"
"La regla 5 deja de mandarle leer la linea~asistente/services.py~   boton vale como aceptacion. NO lo deduzcas~   boton vale como aceptacion. Deducelo~$L.test_el_prompt_le_manda_leer_la_linea_y_no_deducir"
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
