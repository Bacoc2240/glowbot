"""Siembra y reseteo de los establecimientos de demostración (RF-23).

Toda la lógica vive aquí y no en los comandos, por el mismo motivo que el
resto del proyecto: los comandos son adaptadores de línea de órdenes, y lo
que hay que poder probar es la regla, no el `print`.

── Por qué el reseteo borra POR ANTIGÜEDAD y no por reloj ──

La tentación es vaciar el demo cada hora en punto. Sería un error: el
prospecto que está conversando a las 10:59 ve desaparecer su cita recién
creada delante de él, justo en el instante en que el producto tenía que
lucir. Se borra lo que lleva más de `HORAS_VIDA` horas sin tocarse, de modo
que ninguna sesión viva se interrumpe y aun así nada se acumula.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from agenda.models import Cita
from agenda.services import (AgendaService, CitaEnElPasado, SlotNoDisponible,
                             TelefonoVetado)
from asistente.models import ConversacionIA

from .demo import (CLIENTES_DEMO, DEMOS, DIAS_LABORALES, DIAS_SEMBRADOS,
                   EMAIL_PROPIETARIO_DEMO, JORNADA, OCUPACION)
from .models import (ClienteFinal, Establecimiento, HorarioBase, Profesional,
                     ProfesionalServicio, Servicio)

# Cuánto sobrevive un dato del demo antes de que el reseteo lo recoja.
# Dos horas cubre de sobra una sesión de demostración —que dura minutos— y
# mantiene la agenda limpia para el siguiente visitante.
HORAS_VIDA = 2


def propietario_demo():
    """El usuario de sistema que figura como dueño de los demos.

    Se le fija una contraseña INUTILIZABLE, no una contraseña débil: con
    `set_unusable_password` Django no acepta ninguna credencial contra esa
    cuenta, así que el usuario existe para satisfacer la clave foránea de
    `Establecimiento.propietario` y para nada más. Una cuenta de demo con
    contraseña real sería una puerta abierta al panel de los demos y, si
    alguien reutilizara el correo, algo peor.
    """
    from cuentas.models import Usuario

    usuario = Usuario.objects.filter(email=EMAIL_PROPIETARIO_DEMO).first()
    if usuario is None:
        usuario = Usuario(email=EMAIL_PROPIETARIO_DEMO, rol=Usuario.Rol.ADMIN)
    usuario.set_unusable_password()
    usuario.is_active = False
    usuario.save()
    return usuario


def _dias_laborales_proximos(cuantos):
    """Los próximos `cuantos` días que el negocio atiende, empezando mañana.

    Empieza mañana y no hoy a propósito: sembrar ocupación en horas que ya
    pasaron produce citas que `reservar` rechaza, y el resultado dependería
    de la hora a la que corriera el cron.
    """
    dias, dia = [], timezone.localdate() + timedelta(days=1)
    while len(dias) < cuantos:
        if dia.weekday() in DIAS_LABORALES:
            dias.append(dia)
        dia += timedelta(days=1)
    return dias


@transaction.atomic
def sembrar_estructura(definicion):
    """Crea o actualiza el establecimiento, su gente y sus servicios.

    Idempotente: correrlo dos veces no duplica nada. Se apoya en el slug,
    que es único, y en `get_or_create` para todo lo demás.
    """
    est, _ = Establecimiento.objects.update_or_create(
        slug=definicion["slug"],
        defaults={
            "propietario": propietario_demo(),
            "nombre": definicion["nombre"],
            "tipo": definicion["tipo"],
            "municipio": definicion["municipio"],
            "telefono": definicion["telefono"],
            "plan": Establecimiento.Plan.PREMIUM,
            "activo": True,
            "es_demo": True,
        },
    )

    profesionales = []
    for nombre in definicion["profesionales"]:
        prof, _ = Profesional.objects.get_or_create(
            establecimiento=est, nombre=nombre,
            defaults={"activo": True},
        )
        profesionales.append(prof)
        for dia in DIAS_LABORALES:
            for inicio, fin in JORNADA:
                HorarioBase.objects.get_or_create(
                    profesional=prof, dia_semana=dia,
                    hora_inicio=inicio, hora_fin=fin,
                )

    servicios = []
    for nombre, duracion in definicion["servicios"]:
        serv, _ = Servicio.objects.get_or_create(
            establecimiento=est, nombre=nombre,
            defaults={"duracion_min": duracion, "activo": True},
        )
        servicios.append(serv)
        # Todos los profesionales hacen todos los servicios. En un demo, una
        # combinación no cubierta solo produce un \"ese profesional no hace
        # ese servicio\" que el prospecto lee como un fallo del sistema.
        for prof in profesionales:
            ProfesionalServicio.objects.get_or_create(
                profesional=prof, servicio=serv)

    for nombre, telefono in CLIENTES_DEMO:
        ClienteFinal.objects.get_or_create(
            establecimiento=est,
            telefono=telefono,
            nombre=ClienteFinal.normalizar_nombre(nombre),
            defaults={
                "acepta_datos": True,
                "fecha_consentimiento": timezone.now(),
                "version_aviso": "demo",
                # AUTOSERVICIO exige registrador NULL (ck_consentimiento_
                # origen_coherente). Es además el origen honesto: nadie
                # declaró nada por estas personas, que no existen.
                "origen_consentimiento":
                    ClienteFinal.OrigenConsentimiento.AUTOSERVICIO,
            },
        )
    return est, profesionales, servicios


def sembrar_ocupacion(est):
    """Llena parte de la agenda para que el demo tenga algo que enseñar.

    Devuelve cuántas citas se crearon. Las colisiones se ignoran en
    silencio, y aquí sí es correcto: a diferencia de `repetir_semanal`
    —donde una fecha saltada le deja un hueco invisible a un cliente real—
    aquí nadie espera nada. Que un día se siembren siete citas y otro seis
    no le importa a nadie.
    """
    profesionales = list(
        Profesional.objects.del_establecimiento(est).order_by("id"))
    servicios = list(
        Servicio.objects.del_establecimiento(est).order_by("id"))
    clientes = list(
        ClienteFinal.objects.del_establecimiento(est).order_by("id"))
    if not (profesionales and servicios and clientes):
        return 0

    creadas, n = 0, 0
    for indice_dia, dia in enumerate(_dias_laborales_proximos(DIAS_SEMBRADOS)):
        patron = OCUPACION.get(indice_dia, {})
        for indice_prof, horas in patron.items():
            if indice_prof >= len(profesionales):
                continue
            prof = profesionales[indice_prof]
            for hora in horas:
                try:
                    AgendaService.reservar(
                        establecimiento=est,
                        profesional=prof,
                        # Se rota el servicio para que la agenda no se vea
                        # toda igual; el módulo evita salirse de la lista.
                        servicio=servicios[n % len(servicios)],
                        cliente=clientes[n % len(clientes)],
                        dia=dia,
                        hora_inicio=hora,
                        canal=Cita.Canal.MANUAL,
                        # El tope de citas abiertas se salta porque seis
                        # clientes ficticios sostienen toda la agenda: con el
                        # tope puesto, la siembra se cortaría en la tercera.
                        respetar_tope=False,
                        antelacion_min=0,
                    )
                    creadas += 1
                except (SlotNoDisponible, CitaEnElPasado, TelefonoVetado):
                    pass
                n += 1
    return creadas


def resetear(ahora=None):
    """Borra lo caduco del demo y repone la ocupación. Devuelve un parte.

    El filtro es SIEMPRE `es_demo=True`, nunca el slug. Un slug se teclea
    mal; la bandera no. Equivocarse aquí significa vaciarle la agenda a un
    negocio real, así que el borrado no admite un identificador que un dedo
    pueda cambiar.
    """
    ahora = ahora or timezone.now()
    corte = ahora - timedelta(hours=HORAS_VIDA)
    demos = Establecimiento.objects.filter(es_demo=True)

    citas = Cita.objects.filter(
        establecimiento__in=demos, creado_en__lt=corte)
    borradas_citas = citas.count()
    citas.delete()

    convs = ConversacionIA.objects.filter(
        establecimiento__in=demos, actualizado_en__lt=corte)
    borradas_convs = convs.count()
    convs.delete()

    repuestas = sum(sembrar_ocupacion(est) for est in demos)
    return {
        "citas_borradas": borradas_citas,
        "conversaciones_borradas": borradas_convs,
        "citas_repuestas": repuestas,
    }
