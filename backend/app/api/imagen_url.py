"""Imagen por URL para el editor de correo (`/api/emails/imagen-url`).

Bart pega el enlace de una imagen (suya, de sus webs) y va en el `<img>` TAL
CUAL: no se descarga para guardarla, no se rehospeda, no se sube a ninguna
biblioteca. Aquí solo se COMPRUEBA antes de insertarla: HTTPS, que responda
200 con `Content-Type: image/*`, cuánto pesa (más de 1 MB se avisa) y que no
apunte a la red interna (`red.comprobar_url`).
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/emails/imagen-url", tags=["emails"])
