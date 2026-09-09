# Envío de facturas por email en lote — `enviar_facturas_lote.ps1`

Script PowerShell **independiente** para enviar por email en lote las facturas
ya emitidas, **conduciendo el envío que BoHub ya tiene** (F-1, PR #359): correo
**nuevo** al cliente, con el PDF de la factura y el idioma que resuelve la
cascada de la app. **No** es una feature ni toca la app; no reimplementa nada de
correo ni de PDF.

El fichero `.ps1` está en **UTF-8 con BOM** (Windows PowerShell 5.1 rompe los
acentos sin BOM).

---

## Respuesta directa a la pregunta clave (¿se puede enviar por API sin la previsualización interactiva?)

**SÍ.** La «previsualización obligatoria» de F-1 es solo de la interfaz. Por API:

- La **previsualización** es un `GET` sin efectos
  (`…/email-preview`): devuelve destinatario, asunto, idioma, cuerpo y alias.
- El **envío** es `POST …/email` con `confirm: true` en el cuerpo. Ese `confirm`
  es lo único que exige el backend (un `409/400 confirmation_required` si falta);
  **no** hace falta haber llamado antes a la previsualización. El script llama al
  `GET` de preview solo para **obtener** el destinatario/asunto/idioma/cuerpo
  propuestos y luego hace el `POST` con `confirm:true`.

Por tanto el lote es **scriptable** sin interacción de UI.

---

## Cómo se rellena el CSV

Una columna con el número de pedido **web desnudo** (una fila por pedido):

```csv
numero_pedido
99928
9544
5773
```

## Cómo se ejecuta

**1) Previsualización (por defecto, NO envía nada):**

```powershell
.\enviar_facturas_lote.ps1 -BaseUrl https://tu-dominio -Csv .\pedidos_enviar.csv
```

Imprime, por pedido: cliente, email destinatario, idioma que se resolvería,
asunto, y su estado (listo / sin factura / ya enviada / sin email / no
encontrado). No envía nada.

**2) Enviar de verdad (correo NUEVO al cliente; pide confirmación):**

```powershell
.\enviar_facturas_lote.ps1 -BaseUrl https://tu-dominio -Csv .\pedidos_enviar.csv -Apply
```

Muestra los destinatarios y espera que escribas `SI`. Envía de una en una, con
pausa, imprimiendo el resultado. **Se detiene ante el primer error.** Al final:
resumen (enviadas, saltadas, fallidas) y un **log** en fichero. Son clientes
reales y el envío es irreversible → revisa siempre el dry-run primero.

---

## Contrato documentado (Parte 1) — leído del código

1. **Envío (F-1):** `POST /api/erp/factusol/documents/facturas/{serie}/{codigo}/email`.
   Identifica la factura ya emitida por **serie + código** de FACTUSOL (no por el
   UUID del pedido). El script saca serie/código con
   `GET /api/erp/orders/{order_id}/factusol-invoice-ref` (y el UUID del pedido de
   `GET /api/erp/orders?limit=500`, cruzando por el sufijo del `order_number`).
   Cuerpo: `{confirm:true, to:[…], subject, body_text, lang, from_alias,
   reply_to_message_id}`.
2. **Sin previsualización interactiva:** sí se puede (ver arriba): `confirm:true`
   es lo único obligatorio.
3. **Destinatario:** el email del **contacto** del pedido (`preview.to`). Si el
   pedido no tiene email, `preview.to` viene vacío → el script lo **salta** y
   avisa (no inventa destinatario).
4. **Idioma:** lo resuelve la **cascada** de la app automáticamente
   (selector→pedido→cliente→país→empresa→es); el script usa `preview.lang` tal
   cual, sin indicarlo.
5. **Asunto y adjunto:** el asunto es el de la plantilla de F-1, «**Factura
   {nº}**» (referencia el número de factura; el cuerpo incluye además la
   referencia del pedido). Adjunta el PDF de la factura en el idioma resuelto
   (`preview.attachment_filename`). El endpoint admite `subject` propio si algún
   día se quiere forzar el nº de pedido en el asunto.
6. **Idempotencia:** el envío deja un evento de auditoría en el **timeline** del
   pedido con `action = "erp.invoice_emailed"`. El script consulta
   `GET /api/erp/orders/{id}/timeline?types=audit` y **salta** las que ya tengan
   ese evento. Aviso: BoHub solo conoce los envíos que hizo **él**; si a un
   cliente le llegó la factura por otra vía, BoHub no lo sabe.

Login: `POST /api/auth/login` `{email,password}` → `access_token` (Bearer, 8 h);
aborta si el usuario tiene 2FA. Mismo esquema que `emitir_facturas_lote.ps1`.

## Alcance / seguridad

- Usa **solo** el endpoint de envío existente de F-1. **Correo nuevo** al cliente
  (`reply_to_message_id = null`): no responde a hilos ni toca el emparejamiento
  de hilos de F-7.
- Dry-run por defecto; `-Apply` va aparte y con confirmación por teclado.
- **No reenvía** facturas ya enviadas (timeline). Se detiene ante el primer error.
- La contraseña se pide con `-AsSecureString` y solo se convierte a texto en el
  instante del login.
