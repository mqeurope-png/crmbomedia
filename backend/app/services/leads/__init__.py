"""Respuesta a leads · Fase 1.

- `clasificador`: idioma, interés, spam y confianza de un lead; el proveedor
  (IA o palabras clave) va detrás de una interfaz.
- `plantillas`: el mapa interés × idioma → plantilla de `email_templates`.
- `remitentes`: la dirección de la web por la que entró el lead.
- `registro`: la fila de `lead_classifications` y la copia en el contacto.
- `eventos`: el evento `lead.received` que dispara los workflows, desde los
  formularios web y desde las notas «form note» de AgileCRM.

La Fase 1 NO envía ni un correo al cliente: clasifica, deja un borrador, coloca
en el pipeline y avisa a una persona. Todo lo demás es un workflow montado
sobre el motor (`app/workflows`).
"""
