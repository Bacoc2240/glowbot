GlowBot — Sprint 4: Frontend y diferenciales
Plataforma SaaS de agendamiento inteligente para el sector de cuidado
personal y belleza. Proyecto productivo SENA — Tecnología ADSO.
Wilson Vergara Duarte — Ficha 2834885 — Saravena, Arauca, 2026.
Novedades del Sprint 4
Frontend móvil-primero con plantillas Django + Alpine.js (Capa 1).
Panel: login, agenda del día, servicios/equipo, horarios flexibles.
Página pública del chat (el enlace que se comparte por WhatsApp).
Horarios flexibles: horario semanal, excepciones por fecha (RF-16),
bloqueos puntuales y recurrentes, día completo o por franjas (RF-14/15).
NotificacionService: enlaces wa.me prellenados al WhatsApp del
profesional cuando un cliente cancela (RF-13).
13 pruebas nuevas. Total del proyecto: 41 pruebas, todas pasando.
Páginas
/panel/login ingreso del administrador
/panel agenda del día + notificaciones
/panel/servicios servicios y equipo
/panel/horarios horario semanal, excepciones y bloqueos
/p/{slug} chat público del cliente final (enlace compartible)
Puesta en marcha
python -m venv .venv && .venv\Scripts\activate (Windows)
pip install -r requirements.txt
copia .env.ejemplo como .env y completa credenciales + ANTHROPIC_API_KEY
createdb glowbot
python manage.py migrate
python manage.py runserver
Ejecutar las pruebas
python manage.py test agenda.tests asistente.tests web.tests -v2
Endpoints nuevos (Sprint 4)
GET/PUT /api/v1/profesionales/{id}/horarios horario base semanal
GET/POST /api/v1/profesionales/{id}/excepciones horario especial por fecha
DELETE /api/v1/excepciones/{id}
GET/POST /api/v1/profesionales/{id}/bloqueos días libres y franjas
DELETE /api/v1/bloqueos/{id}
GET /api/v1/notificaciones alertas con enlace wa.me
