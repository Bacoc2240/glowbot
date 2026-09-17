"""Pruebas del guardián de rechazos del ciclo del asistente.

Origen: defecto reportado por el primer cliente en producción. Cancelaba
una cita, volvía a agendar el mismo cupo y pedía cancelar de nuevo; el
asistente respondía «tu cita fue cancelada» y la cita seguía confirmada en
la agenda.

El backend nunca se equivocó: rechazaba la operación correctamente. El
defecto estaba en el ciclo de `procesar_mensaje`, que tras un rechazo
dejaba que cualquier frase del modelo cerrara el turno y se la devolvía al
cliente tal cual.

Las 609 pruebas anteriores no lo veían porque cada capa hace bien su
trabajo por separado: el defecto vive en la costura entre el rechazo del
backend y el cierre del turno.
"""
from datetime import time, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from agenda.models import Cita
from asistente.services import IAService
from cuentas.models import Usuario
from negocios.clientes import ClienteService
from negocios.models import (ClienteFinal, Establecimiento, HorarioBase,
                             Profesional, ProfesionalServicio, Servicio)


class BaseAsistente(TestCase):
    def setUp(self):
        usuario = Usuario.objects.create_user(
            email="duena@ejemplo.com", password="Clave12345",
            rol=Usuario.Rol.ADMIN)
        self.est = Establecimiento.objects.create(
            propietario=usuario, nombre="Barbería",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3101112233",
            municipio="Saravena")
        self.prof = Profesional.objects.create(
            establecimiento=self.est, nombre="Eduardo")
        self.serv = Servicio.objects.create(
            establecimiento=self.est, nombre="Corte clásico", duracion_min=30)
        ProfesionalServicio.objects.create(
            profesional=self.prof, servicio=self.serv)
        for dia in range(7):
            HorarioBase.objects.create(
                profesional=self.prof, dia_semana=dia,
                hora_inicio=time(8, 0), hora_fin=time(18, 0))
        self.telefono = "3151112233"
        self.cliente = ClienteService.registrar_con_consentimiento(
            establecimiento=self.est, nombre="Pedro", telefono=self.telefono,
            origen=ClienteFinal.OrigenConsentimiento.AUTOSERVICIO)

    def _cita(self, dias=3, hora=time(10, 0)):
        fin = time(hora.hour, hora.minute + 30) if hora.minute == 0 \
            else time(hora.hour + 1, 0)
        return Cita.objects.create(
            establecimiento=self.est, profesional=self.prof,
            servicio=self.serv, cliente=self.cliente,
            fecha=timezone.localdate() + timedelta(days=dias),
            hora_inicio=hora, hora_fin=fin)

    def _turno(self, respuestas, mensaje="cancela mi cita", sesion="s1"):
        """Corre un turno con respuestas encoladas del modelo."""
        with patch.object(IAService, "_llamar_claude",
                          side_effect=[(r, 10, 10) for r in respuestas]):
            return IAService.procesar_mensaje(self.est, sesion, mensaje)


class RechazoNoCierraElTurnoTest(BaseAsistente):
    """El caso exacto que reportó el cliente."""

    def test_id_obsoleto_y_prosa_no_mienten_al_cliente(self):
        vieja = self._cita()
        vieja.estado = Cita.Estado.CANCELADA_CLIENTE
        vieja.save()
        nueva = self._cita()  # mismo cupo, cita nueva

        r = self._turno([
            # 1) el modelo reemite el id VIEJO, tomado de su propio historial
            '{"intencion":"cancelar_cita","telefono":"%s","cita_id":%d}'
            % (self.telefono, vieja.id),
            # 2) tras el rechazo, contesta en prosa afirmando el éxito
            "Listo, tu cita fue cancelada. ¡Esperamos verte pronto!",
            # 3) el guardián lo devuelve al modelo, que insiste
            "Ya quedó cancelada.",
        ])

        nueva.refresh_from_db()
        self.assertEqual(
            nueva.estado, Cita.Estado.CONFIRMADA,
            "La cita no debía cancelarse: el id era de otra cita")
        self.assertNotIn("cancelada", r["respuesta"].lower(),
                         "El asistente afirmó una cancelación que no ocurrió")

    def test_el_modelo_puede_corregirse_y_cancelar_de_verdad(self):
        """El guardián no bloquea: devuelve el control al modelo. Si este
        reemite la intención correcta, la cancelación se ejecuta."""
        vieja = self._cita()
        vieja.estado = Cita.Estado.CANCELADA_CLIENTE
        vieja.save()
        nueva = self._cita()

        r = self._turno([
            '{"intencion":"cancelar_cita","telefono":"%s","cita_id":%d}'
            % (self.telefono, vieja.id),
            '{"intencion":"cancelar_cita","telefono":"%s","cita_id":%d}'
            % (self.telefono, nueva.id),
        ])

        nueva.refresh_from_db()
        self.assertEqual(nueva.estado, Cita.Estado.CANCELADA_CLIENTE)
        self.assertIn("cancelada", r["respuesta"].lower())

    def test_el_rechazo_dice_que_el_id_ya_estaba_cancelado(self):
        """Un «no corresponde» genérico llevaba al modelo a concluir que ya
        estaba hecho. El mensaje nombra el caso y lista las citas vigentes."""
        vieja = self._cita()
        vieja.estado = Cita.Estado.CANCELADA_CLIENTE
        vieja.save()
        nueva = self._cita()

        final, feedback = IAService._ejecutar_intencion(
            self.est,
            {"intencion": "cancelar_cita", "telefono": self.telefono,
             "cita_id": vieja.id})

        self.assertIsNone(final)
        self.assertIn("ya estaba cancelada", feedback)
        self.assertIn("NO has cancelado nada", feedback)
        self.assertIn(str(nueva.id), feedback)


class RealimentacionInformativaTest(BaseAsistente):
    """El guardián NO puede romper los flujos donde preguntar es correcto."""

    def test_varias_citas_el_modelo_pregunta_y_cierra(self):
        """Con dos citas activas el backend pide que se pregunte cuál. Ahí
        cerrar el turno en prosa es lo correcto, y debe seguir pudiendo."""
        self._cita(dias=3)
        self._cita(dias=5, hora=time(11, 0))

        r = self._turno([
            '{"intencion":"cancelar_cita","telefono":"%s"}' % self.telefono,
            "Tienes dos citas. ¿Cuál quieres cancelar?",
        ])
        self.assertEqual(
            r["respuesta"], "Tienes dos citas. ¿Cuál quieres cancelar?",
            "Debe salir la frase del modelo, no el mensaje degradado: con la "
            "bandera pegada el turno agota iteraciones y el cliente recibe "
            "«tuvimos un inconveniente» en vez de la pregunta")
        self.assertEqual(
            Cita.objects.del_establecimiento(self.est)
            .filter(estado=Cita.Estado.CONFIRMADA).count(), 2,
            "No debía cancelarse ninguna sin que el cliente escogiera")

    def test_sin_citas_el_modelo_informa_y_cierra(self):
        r = self._turno([
            '{"intencion":"cancelar_cita","telefono":"%s"}' % self.telefono,
            "No encontré citas confirmadas con ese número.",
        ])
        self.assertIn("No encontré", r["respuesta"])

    def test_la_marca_no_se_le_inyecta_al_modelo(self):
        """`[RECHAZO]` clasifica la realimentación para el bucle; no es texto
        que el modelo deba leer ni que deba quedar en el historial.

        Se comprueba sobre un flujo de RECHAZO real —si no, no hay marca que
        filtrar— y sobre lo que quedó guardado, que es lo que el modelo verá
        en los turnos siguientes."""
        from asistente.models import ConversacionIA
        vieja = self._cita()
        vieja.estado = Cita.Estado.CANCELADA_CLIENTE
        vieja.save()
        self._cita()

        self._turno([
            '{"intencion":"cancelar_cita","telefono":"%s","cita_id":%d}'
            % (self.telefono, vieja.id),
            "Entiendo, déjame verificar.",
            "¿Me confirmas la fecha de la cita que quieres cancelar?",
        ], sesion="marca")

        conv = ConversacionIA.objects.get(
            establecimiento=self.est, session_id="marca")
        guardado = " ".join(m.get("content", "") for m in conv.mensajes)
        self.assertNotIn("[RECHAZO]", guardado)


class LimiteDelGuardianTest(BaseAsistente):
    """Hasta dónde llega el guardián, y dónde no llega. A propósito.

    El guardián cubre los rechazos que exigen REINTENTO. No cubre —ni
    pretende— que el modelo mienta después de recibir información correcta:
    para eso haría falta detectar la mentira en el texto, que es el problema
    que ataca `responde_sin_haber_mirado` y que solo cubre disponibilidad.

    Esta prueba fija el límite para que quede escrito, no para celebrarlo.
    """

    def test_la_falta_de_consentimiento_cierra_en_prosa(self):
        """El backend rechaza la reserva, pero le da al modelo algo cierto
        que decir: pídele al cliente que pulse el botón. Cerrar el turno ahí
        es correcto, y por eso NO se marca como rechazo."""
        dia = (timezone.localdate() + timedelta(days=4)).isoformat()
        r = self._turno([
            '{"intencion":"reservar","servicio_id":%d,"profesional_id":%d,'
            '"dia":"%s","hora":"09:00",'
            '"cliente":{"nombre":"Pedro","telefono":"%s"}}'
            % (self.serv.id, self.prof.id, dia, self.telefono),
            "Para agendar necesito que pulses el botón de aceptación.",
        ], mensaje="agéndame mañana a las 9")

        self.assertIn("botón", r["respuesta"])
        self.assertEqual(
            Cita.objects.del_establecimiento(self.est).count(), 0,
            "No debía crearse ninguna cita sin consentimiento")

    def test_el_slot_ocupado_sigue_ofreciendo_alternativas(self):
        """Regresión del primer intento de arreglo: tratar todo rechazo como
        bloqueante rompió este flujo, que es de los más usados."""
        ocupada = self._cita(dias=4, hora=time(9, 0))
        dia = ocupada.fecha.isoformat()
        r = self._turno([
            '{"intencion":"consultar_disponibilidad","servicio_id":%d,'
            '"profesional_id":%d,"dia":"%s"}'
            % (self.serv.id, self.prof.id, dia),
            "Tengo libre a las 9:30. ¿Te sirve?",
        ], mensaje="¿hay campo a las 9?")
        self.assertIn("9:30", r["respuesta"])


class RechazoPorIdAjenoTest(BaseAsistente):
    """Ramas que las pruebas anteriores no alcanzaban.

    El arnés de mutación las encontró: se podía borrar la marca de rechazo
    de la rama genérica, o quitarle el filtro de tenant a la búsqueda del
    id previo, sin que ninguna prueba se cayera.
    """

    def test_un_id_que_no_existe_tambien_bloquea_el_cierre_en_prosa(self):
        """Rama `previa is None`: el id no pertenece a este teléfono ni a
        ninguna cita suya. También es rechazo y también debe reintentar."""
        nueva = self._cita()
        r = self._turno([
            '{"intencion":"cancelar_cita","telefono":"%s","cita_id":999999}'
            % self.telefono,
            "Listo, tu cita fue cancelada.",
            "Ya quedó cancelada.",
        ])
        nueva.refresh_from_db()
        self.assertEqual(nueva.estado, Cita.Estado.CONFIRMADA)
        self.assertNotIn("cancelada", r["respuesta"].lower(),
                         "El asistente afirmó una cancelación que no ocurrió")

    def test_no_mira_las_citas_de_otro_establecimiento(self):
        """La búsqueda del id previo va por `del_establecimiento`.

        Sin ese filtro, un id de OTRO negocio con el mismo teléfono haría
        que el mensaje dijera «la cita N ya estaba cancelada» refiriéndose a
        una cita ajena: una fuga entre tenants dentro de un mensaje de
        error, que es donde menos se mira.
        """
        otro_usuario = Usuario.objects.create_user(
            email="otra@ejemplo.com", password="Clave12345",
            rol=Usuario.Rol.ADMIN)
        otro = Establecimiento.objects.create(
            propietario=otro_usuario, nombre="Otra Barbería",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3109998877")
        prof2 = Profesional.objects.create(establecimiento=otro, nombre="Luis")
        serv2 = Servicio.objects.create(
            establecimiento=otro, nombre="Corte", duracion_min=30)
        cli2 = ClienteFinal.objects.create(
            establecimiento=otro, nombre="Pedro", telefono=self.telefono,
            acepta_datos=True)
        ajena = Cita.objects.create(
            establecimiento=otro, profesional=prof2, servicio=serv2,
            cliente=cli2, fecha=timezone.localdate() + timedelta(days=3),
            hora_inicio=time(15, 0), hora_fin=time(15, 30),
            estado=Cita.Estado.CANCELADA_CLIENTE)

        self._cita()  # una cita propia, para que haya lista que ofrecer
        final, feedback = IAService._ejecutar_intencion(
            self.est,
            {"intencion": "cancelar_cita", "telefono": self.telefono,
             "cita_id": ajena.id})

        self.assertIsNone(final)
        self.assertNotIn("ya estaba cancelada", feedback,
                         "Se está mirando una cita de otro establecimiento")
