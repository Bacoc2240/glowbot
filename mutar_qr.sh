#!/usr/bin/env bash
# Arnes de mutacion del paquete del codigo QR.
#
# Reintroduce cada defecto a proposito y comprueba que la prueba que dice
# protegerlo efectivamente falla. Una prueba que pasa con el codigo roto no
# prueba nada; este guion es el que decide si el paquete se entrega.
#
# La restauracion se hace copiando desde una carpeta limpia y no deshaciendo
# la sustitucion, para que una interrupcion no pueda dejar codigo mutado en
# el arbol de trabajo.
#
# Uso:  bash mutar_qr.sh [indice_inicial] [indice_final]

set -u
LIMPIO=/tmp/limpio
Q=negocios.tests.CodigoQrEnlacePublicoTests
W=web.tests.TarjetaCodigoQrTests
PY=.venv/bin/python

restaurar() {
  cp "$LIMPIO/negocios/qr.py"           negocios/qr.py
  cp "$LIMPIO/negocios/api.py"          negocios/api.py
  cp "$LIMPIO/templates/web/panel.html" templates/web/panel.html
}
trap restaurar EXIT INT TERM

# titulo | archivo | cadena vieja | cadena nueva | pruebas separadas por coma
MUTACIONES=(
"El generador ignora el enlace y siempre codifica la portada|negocios/qr.py|    codigo.add_data(enlace)|    codigo.add_data(\"https://glowbot.com.co\")|$Q.test_el_codigo_representa_exactamente_el_enlace_que_se_le_pide,$Q.test_dos_direcciones_distintas_producen_codigos_distintos,$Q.test_el_codigo_representa_el_mismo_enlace_que_se_muestra"
"Se elimina el margen blanco que exige la norma|negocios/qr.py|MARGEN_MODULOS = 4|MARGEN_MODULOS = 0|$Q.test_conserva_el_margen_blanco_que_exige_la_norma"
"Se baja la correccion de errores de H a M|negocios/qr.py|from qrcode.constants import ERROR_CORRECT_H|from qrcode.constants import ERROR_CORRECT_M as ERROR_CORRECT_H|$Q.test_el_nivel_de_correccion_alto_se_mantiene,$Q.test_el_codigo_representa_exactamente_el_enlace_que_se_le_pide"
"El enlace vuelve a depender del dominio por el que se entro|negocios/api.py|settings.SITIO_URL.rstrip('/')|'https://glowbot-production.up.railway.app'|$Q.test_el_enlace_sale_de_la_configuracion_y_no_del_dominio_visitado"
"Se deja de limpiar la barra final de SITIO_URL|negocios/api.py|settings.SITIO_URL.rstrip('/')|settings.SITIO_URL|$Q.test_una_barra_sobrante_en_la_configuracion_no_parte_el_enlace"
"El QR se calcula sobre el slug pelado y no sobre el enlace completo|negocios/api.py|data_uri_del_enlace(enlace_publico_de(obj))|data_uri_del_enlace(obj.slug)|$Q.test_el_codigo_representa_el_mismo_enlace_que_se_muestra,$Q.test_al_cambiar_la_direccion_el_codigo_deja_de_ser_el_anterior"
"El boton de descarga queda desconectado (la funcion sigue definida)|templates/web/panel.html|@click=\"descargarQr()\" |@nada=\"\" |$W.test_la_tarjeta_muestra_el_codigo_y_ofrece_descargarlo"
"El panel vuelve a calcular el enlace en el navegador|templates/web/panel.html|return this.est ? this.est.enlace_publico : \"\"|return this.est ? this.origen() + \"/p/\" + this.est.slug : \"\"|$W.test_el_panel_muestra_el_enlace_que_arma_el_servidor"
"Tras cambiar la direccion se parchea el slug sin releer el establecimiento|templates/web/panel.html|      await this.cargarEstablecimiento();\n      this.okSlug|      this.est.slug = d.slug;\n      this.okSlug|$W.test_al_guardar_la_direccion_la_pantalla_relee_el_negocio"
"Se retira el aviso de no recortar el borde|templates/web/panel.html|No recortes el borde blanco: sin el, deja de leerse.|Compartelo con tus clientes.|$W.test_la_tarjeta_advierte_que_no_se_recorte_el_borde"
)

desde=${1:-0}
hasta=${2:-${#MUTACIONES[@]}}
total=0; nomuerden=0

for ((i=desde; i<hasta && i<${#MUTACIONES[@]}; i++)); do
  IFS='|' read -r titulo archivo viejo nuevo pruebas <<< "${MUTACIONES[$i]}"
  restaurar
  VIEJO="$viejo" NUEVO="$nuevo" ARCHIVO="$archivo" python3 - <<'PYAP'
import os, sys, pathlib
p = pathlib.Path(os.environ["ARCHIVO"])
t = p.read_bytes().decode("utf-8")
# El terminador se toma del propio archivo: panel.html y api.py usan CRLF
# y una mutacion multilinea escrita con \n no encontraria nada, dando un
# falso "no aplicable" que se confundiria facilmente con "no hay defecto".
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
if [ $nomuerden -gt 0 ]; then
  echo "FALLO: $nomuerden de $total comprobaciones no mordieron"; exit 1
fi
echo "OK: las $total comprobaciones mordieron."
