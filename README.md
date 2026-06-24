GlowBot — Sprint 2: Motor de Agendamiento
Plataforma SaaS de agendamiento inteligente para el sector de cuidado
personal y belleza. Proyecto productivo SENA — Tecnología ADSO.
Wilson Vergara Duarte — Ficha 2834885 — Saravena, Arauca, 2026.
Novedades del Sprint 2
AgendaService: algoritmo de disponibilidad de 3 capas (horario base →
excepción → bloqueo) + reserva atómica con select_for_update().
12 pruebas automatizadas del motor de agenda (todas pasan).
Endpoints: CRUD de servicios y profesionales, disponibilidad y citas.
Puesta en marcha
python -m venv .venv && .venv\Scripts\activate (Windows)
pip install -r requirements.txt
createdb glowbot
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
Ejecutar las pruebas
python manage.py test agenda.tests -v2
Endpoints (Sprint 1 + 2)
POST /api/v1/auth/registro crea cuenta + establecimiento
POST /api/v1/auth/login JWT access + refresh
GET /api/v1/servicios lista servicios (RF-04)
POST /api/v1/servicios crea servicio
GET /api/v1/profesionales lista profesionales (RF-05)
POST /api/v1/profesionales crea profesional (valida plan)
GET /api/v1/disponibilidad slots libres por 3 capas (RF-06)
GET /api/v1/citas?fecha=&profesional= calendario (RF-07)
POST /api/v1/citas reserva (409 si ocupado, RF-11)
PATCH /api/v1/citas/{id}/cancelar cancela y libera slot (RF-08)
Documentos de referencia: SRS v1.0, Diccionario de Datos v1.0,
