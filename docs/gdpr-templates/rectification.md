# Plantilla — Respuesta a solicitud de rectificación (art. 16 RGPD)

**Asunto:** Confirmación de rectificación de sus datos personales

Estimado/a {{NOMBRE_TITULAR}},

En respuesta a su solicitud del {{FECHA_SOLICITUD}}, le informamos de que
{{RAZON_SOCIAL}} ha procedido a **rectificar los datos personales**
indicados, en ejercicio del derecho previsto en el artículo 16 del
RGPD.

**Cambios aplicados:**

- Campo: `{{CAMPO}}`
- Valor anterior: `{{VALOR_ANTERIOR}}`
- Valor actualizado: `{{VALOR_NUEVO}}`
- Fecha de aplicación: `{{FECHA_RECTIFICACION}}`

Si los datos rectificados han sido comunicados previamente a destinatarios
externos (Brevo, AgileCRM, Freshdesk, FactuSOL), notificaremos la
rectificación a dichos destinatarios salvo que sea imposible o suponga un
esfuerzo desproporcionado.

Le recordamos que puede ejercer en cualquier momento los derechos
adicionales que la normativa le reconoce, así como presentar reclamación
ante la AEPD (www.aepd.es).

Atentamente,

{{NOMBRE_OPERADOR}}
{{CARGO}}
{{EMAIL_CONTACTO_DPO}}

---

**Datos internos (no enviar al titular):**

- ID solicitud CRM: `{{REQUEST_ID}}`
- Endpoint utilizado: `PATCH /api/contacts/{{CONTACT_ID}}`
- Procesada por: `{{ADMIN_EMAIL}}`
- Fecha proceso: `{{COMPLETED_AT}}`
