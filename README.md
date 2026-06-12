# GlowBot — Sprint 1: Núcleo del sistema

Plataforma SaaS de agendamiento inteligente para el sector de cuidado
personal y belleza. Proyecto productivo SENA — Tecnología ADSO.
Wilson Vergara Duarte — Ficha 2834885 — Saravena, Arauca, 2026.

## Puesta en marcha
1. python -m venv .venv && .venv\Scripts\activate   (Windows)
2. pip install -r requirements.txt
3. copia .env.ejemplo como .env y completa tus credenciales
4. crea la base de datos:  createdb glowbot
5. python manage.py migrate
6. python manage.py createsuperuser
7. python manage.py runserver

## Endpoints disponibles (Sprint 1)
- POST /api/v1/auth/registro  — crea cuenta + establecimiento
- POST /api/v1/auth/login     — JWT (access + refresh)
- POST /api/v1/auth/refresh   — renueva el access token
- /admin/                     — panel de verificación de modelos

Documentos de referencia: SRS v1.0, Diccionario de Datos v1.0,
Especificación de API v1.0, Sistema de Prompts v1.0.
