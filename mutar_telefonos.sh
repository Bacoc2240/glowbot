#!/usr/bin/env bash
# Arnes de mutacion del paquete previo al lanzamiento nacional. Separador: ~
#
# Antes de ejecutarlo:
#   mkdir -p /tmp/l15/negocios /tmp/l15/negocios/migrations /tmp/l15/asistente \
#            /tmp/l15/cuentas /tmp/l15/facturacion /tmp/l15/web /tmp/l15/templates
#   cp negocios/telefonos.py negocios/models.py negocios/clientes.py /tmp/l15/negocios/
#   cp negocios/migrations/0014_canonizar_telefonos.py /tmp/l15/negocios/migrations/
#   cp asistente/services.py   /tmp/l15/asistente/
#   cp cuentas/api.py          /tmp/l15/cuentas/
#   cp facturacion/services.py /tmp/l15/facturacion/
#   cp web/legal.py            /tmp/l15/web/
#   cp templates/web/portada.html templates/web/login.html \
#      templates/web/registro.html templates/web/suscripcion.html /tmp/l15/templates/
set -u
T=negocios.tests_telefonos
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
  cp /tmp/l15/negocios/telefonos.py  negocios/telefonos.py
  cp /tmp/l15/negocios/models.py     negocios/models.py
  cp /tmp/l15/negocios/clientes.py   negocios/clientes.py
  cp /tmp/l15/negocios/migrations/0014_canonizar_telefonos.py \
     negocios/migrations/0014_canonizar_telefonos.py
  cp /tmp/l15/asistente/services.py   asistente/services.py
  cp /tmp/l15/cuentas/api.py          cuentas/api.py
  cp /tmp/l15/facturacion/services.py facturacion/services.py
  cp /tmp/l15/web/legal.py            web/legal.py
  cp /tmp/l15/templates/portada.html     templates/web/portada.html
  cp /tmp/l15/templates/login.html       templates/web/login.html
  cp /tmp/l15/templates/registro.html    templates/web/registro.html
  cp /tmp/l15/templates/suscripcion.html templates/web/suscripcion.html
}
trap restaurar EXIT INT TERM
MUTACIONES=(
"El normalizador acepta cualquier longitud~negocios/telefonos.py~    if len(digitos) != LARGO:~    if False:~$T.NormalizadorTest.test_rechaza_los_que_sobran_o_faltan,$T.NormalizadorTest.test_rechaza_el_fijo_de_siete_digitos"
"El indicativo 57 se quita siempre y destroza el numero~negocios/telefonos.py~    if len(digitos) == LARGO + 2 and digitos.startswith(\"57\"):~    if digitos.startswith(\"57\"):~$T.NormalizadorTest.test_el_57_solo_se_quita_cuando_sobra"
"El alta deja de normalizar y vuelve a duplicar clientes~negocios/clientes.py~        telefono = normalizar(telefono)~        pass~$T.AltaDeClienteTest.test_no_duplica_al_variar_el_formato,$T.AltaDeClienteTest.test_rechaza_un_telefono_invalido"
"save() lanza y congela a los clientes heredados~negocios/models.py~        canonico = normalizar_si_puede(self.telefono)\n        if canonico:~        canonico = normalizar_si_puede(self.telefono) or 1/0\n        if canonico:~$T.AltaDeClienteTest.test_una_fila_heredada_se_puede_seguir_guardando"
"La busqueda deja de normalizar y pierde las citas del cliente~asistente/services.py~        telefono = normalizar_si_puede(telefono)\n        if not telefono:~        if not telefono:~$T.ConsultaPorTelefonoTest.test_encuentra_la_cita_con_el_numero_espaciado"
"El registro publico acepta un telefono cualquiera~cuentas/api.py~            return normalizar(value)~            return value~$T.RegistroEstablecimientoTest.test_telefono_malo_devuelve_400_y_no_500"
"El registro revienta con 500 en vez de responder 400~cuentas/api.py~    def validate_telefono(self, value):~    def _validate_telefono_desactivado(self, value):~$T.RegistroEstablecimientoTest.test_telefono_malo_devuelve_400_y_no_500"
"El servicio de registro deja de exigir la forma canonica~facturacion/services.py~        telefono = normalizar(telefono)~        pass~$T.RegistroEstablecimientoTest.test_el_servicio_normaliza_aunque_no_pase_por_el_serializer,$T.RegistroEstablecimientoTest.test_el_servicio_rechaza_un_telefono_invalido"
"La migracion vuelve a escribir fila por fila y choca~negocios/migrations/0014_canonizar_telefonos.py~        superviviente = filas[0][0]~        superviviente = filas[-1][0]~$T.MigracionCanonizarTest.test_fusiona_duplicados_y_conserva_las_citas"
"La migracion pierde las citas del registro fusionado~negocios/migrations/0014_canonizar_telefonos.py~            Cita.objects.filter(cliente_id=pk).update(cliente_id=superviviente)~            pass~$T.MigracionCanonizarTest.test_fusiona_duplicados_y_conserva_las_citas"
"La migracion fusiona por numero e ignora el nombre~negocios/migrations/0014_canonizar_telefonos.py~        grupos.setdefault((est_id, canonico, nombre), []).append((pk, telefono))~        grupos.setdefault((est_id, canonico), []).append((pk, telefono))~$T.MigracionCanonizarTest.test_no_fusiona_a_dos_personas_del_mismo_numero"
"La migracion inventa digitos en vez de marcar~negocios/migrations/0014_canonizar_telefonos.py~        if not canonico:\n            ClienteFinal.objects.filter(pk=pk).update(telefono_revisar=True)\n            continue~        if not canonico:\n            canonico = (telefono + \"0000000000\")[:10]~$T.MigracionCanonizarTest.test_marca_lo_que_no_puede_arreglar"
"La migracion no marca el establecimiento con fijo~negocios/migrations/0014_canonizar_telefonos.py~            Establecimiento.objects.filter(pk=pk).update(telefono_revisar=True)~            pass~$T.MigracionCanonizarTest.test_marca_el_establecimiento_con_fijo"
"El prompt vuelve a creerse en Saravena~asistente/services.py~        ubicacion = (f\" en {establecimiento.municipio}\"\n                     if establecimiento.municipio else \"\")~        ubicacion = \" en Saravena, Arauca\"~$T.PromptDelAsistenteTest.test_usa_el_municipio_del_establecimiento,$T.PromptDelAsistenteTest.test_sin_municipio_no_inventa_ubicacion"
"La portada vuelve a nombrar el municipio~templates/web/portada.html~  GlowBot · Colombia<br>~  GlowBot · Saravena, Arauca, Colombia<br>~$T.PlantillasTest.test_la_portada_no_menciona_el_municipio"
"Desaparece el correo de contacto de la portada~templates/web/portada.html~privacidad@glowbot.com.co</a><br>~</a><br>~$T.PlantillasTest.test_la_portada_ofrece_el_correo_de_contacto"
"El ojito queda decorativo y nunca muestra la clave~templates/web/login.html~:type=\"verClave ? 'text' : 'password'\"~type=\"password\"~$T.PlantillasTest.test_el_campo_alterna_entre_texto_y_clave"
"Desaparece el ojito del registro~templates/web/registro.html~        <button type=\"button\" class=\"ojo\" @click=\"verClave = !verClave\"~        <button type=\"button\" class=\"otro\" @click=\"nada = 1\"~$T.PlantillasTest.test_el_registro_tiene_boton_de_ver_contrasena"
"Vuelve el titular a la pantalla de suscripcion~templates/web/suscripcion.html~    <div x-show=\"dp.daviplata\"~    <p class=\"muted\">Titular: <b x-text=\"dp.titular\"></b></p>\n    <div x-show=\"dp.daviplata\"~$T.PlantillasTest.test_la_suscripcion_ya_no_muestra_el_titular"
"El aviso legal se queda sin domicilio~web/legal.py~\"domicilio\": os.getenv(\"LEGAL_DOMICILIO\", \"Arauca, Colombia\"),~\"domicilio\": os.getenv(\"LEGAL_DOMICILIO\", \"\"),~$T.AvisoLegalTest.test_el_aviso_sigue_identificando_al_responsable,$T.AvisoLegalTest.test_el_domicilio_es_el_departamento"
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
