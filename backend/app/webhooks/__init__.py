"""Receptores de webhooks entrantes (fuera del prefijo `/api/*`).

La seguridad NO es la sesión del CRM (estos endpoints los llama Internet sin
credenciales) sino la firma del proveedor. Cada receptor verifica su propia
firma (HMAC en WooCommerce, token en Genei) antes de tocar la BD.
"""
