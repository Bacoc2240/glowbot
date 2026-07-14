GlowBot — Sprint 3: Integración de IA
Plataforma SaaS de agendamiento inteligente para el sector de cuidado
personal y belleza. Proyecto productivo SENA — Tecnología ADSO.
Wilson Vergara Duarte — Ficha 2834885 — Saravena, Arauca, 2026.
Novedades del Sprint 3
IAService: asistente conversacional con Claude API (claude-haiku-4-5),
prompt de sistema dinámico por establecimiento y prompt caching.
Principio "la IA propone, el backend dispone": intenciones JSON
validadas por AgendaService antes de tocar la base de datos.
5 capas de defensa anti-alucinación (Sistema de Prompts v1.0).
Zona pública por slug: info del negocio, chat del asistente,
consulta y cancelación de citas por teléfono.
Límite de peticiones del chat: 20/min por IP (HTTP 429).
16 pruebas nuevas del asistente (28 en total con el motor de agenda).
Configuración de la Claude API
Crea tu clave en https://platform.claude.com (Claude Console).
En el archivo .env agrega: ANTHROPIC_API_KEY=tu-clave-aqui
(Opcional) ANTHROPIC_MODEL=claude-haiku-4-5 (valor por defecto)
Puesta en marcha
python -m venv .venv && .venv\Scripts\activate (Windows)
pip install -r requirements.txt
copia .env.ejemplo como .env y completa credenciales + ANTHROPIC_API_KEY
createdb glowbot
python manage.py migrate
python manage.py runserver
Ejecutar las pruebas
python manage.py test agenda.tests asistente.tests -v2
Endpoints nuevos (zona pública, sin autenticación)
GET /api/v1/p/{slug} info pública del establecimiento
POST /api/v1/p/{slug}/chat chat con el asistente IA (RF-10)
POST /api/v1/p/{slug}/citas/consultar próxima cita por teléfono (RF-12)
POST /api/v1/p/{slug}/citas/cancelar cancela y notifica (RF-12, RF-13)
