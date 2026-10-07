# Fotos del embalaje

**Problema (07/10/2026).** `POST /api/erp/orders/{id}/attach-document` guardaba la
foto con `get_document_storage()`. Sin credenciales de HiDrive, eso es
`LocalDocumentStorage` en `erp_uploads_dir` = `/app/uploads/erp`: dentro del
contenedor `api`, **sin volumen**. Cada despliegue borraba las fotos y el
`packing_json.documents` se quedaba con referencias a archivos que ya no
existían (y `url: null`).

| Documento | Antes | Ahora |
|---|---|---|
| Albarán / etiqueta | `shipment_files` + `/opt/crmbo/uploads/erp-shipping` (bind mount) | igual |
| Foto del embalaje | `packing_json.documents` + `/app/uploads/erp` (efímero) | `shipment_files` (`kind = foto`) + el mismo bind mount |

## Cómo funciona ahora

- **Guardar.** `attach-document` (y `POST /shipping-files` con `kind=foto`) pasan
  por `erp/fotos.py`:
  - `normalizar`: JPEG y PNG se guardan tal cual, igual que un PDF. HEIC/HEIF
    (iPhone), WebP (Android) y el resto de imágenes que Pillow sabe leer se
    convierten a **JPEG**, aplicando la orientación de la cámara.
  - Los iPhone usan HEIC, que se lee con `pillow-heif`, nueva dependencia del
    backend.
  - Varias fotos **conviven**: una nueva no reemplaza a las anteriores, a
    diferencia del albarán y la etiqueta.
- **Errores** (siempre con texto claro):
  - 400: archivo vacío;
  - 413: más de 15 MB (la API; nginx deja pasar hasta 16 MB para que el
    mensaje sea el de BoHub);
  - 415: formato no reconocido, o imagen descomunal (bomba de
    descompresión: millones de píxeles en pocos KB);
  - 507: el almacén no pudo guardar.
- **Navegador.** Si la foto pesa más de 2,5 MB, se reduce antes de subirla (lado
  mayor 2560 px, JPEG al 85 %; `lib/fotos.ts`). Si el navegador no sabe leerla
  (HEIC fuera de Safari) se manda tal cual y la convierte el servidor. Solo si
  además pasa de 15 MB (el tope de la API) se avisa sin subir.
- **Ver.**
  - Las fotos salen en la Cola SAT (`fotos` de cada pedido) y en la ficha
    (`GET /shipping-files?kind=foto`), como miniaturas pulsables que abren la
    foto entera.
  - La miniatura la genera el servidor: `…/download?thumb=1` da un JPEG de 320
    px.
- **Cuándo se pueden subir.** En cualquier fase antes de «recogido» (por embalar,
  en preparación, embalado, pendiente de recogida), con el permiso de envíos.
  Después solo se ven.

## Traslado de las fotos antiguas (tarea de una pasada)

No es una migración de Alembic: las migraciones solo cambian el esquema. El
traslado mueve archivos, así que va aparte (`app/erp/fotos_job.py`):

- Se lanza solo al arrancar el `api`, una vez y en segundo plano.
- Pasa las referencias de `packing_json.documents` a `shipment_files`:
  - si el archivo **existe**, en `erp_uploads_dir` o en
    `/opt/crmbo/uploads/erp-shipping/_rescate/`, se copia al almacén de
    expedición y se registra como `kind = foto`;
  - si se **perdió**, se quita la referencia. Queda solo constancia en
    `packing_json.fotos_perdidas` (nombre y fecha, sin ruta), y la ficha avisa
    de volver a subirla.
- No falla nunca: sin la carpeta del almacén o sin nada que mover, no hace
  nada. Es idempotente.
- A mano, desde el contenedor `api`:
  - `python -m app.erp.fotos_job` → vista previa (cuántas se mueven y cuántas
    se perdieron, sin escribir);
  - `python -m app.erp.fotos_job --apply` → lo hace.

### Antes de desplegar: rescatar las fotos que aún están en el contenedor

El despliegue recrea el contenedor `api`, así que lo que hay en `/app/uploads/erp`
se pierde **antes** de que corra el traslado. Para salvar lo que siga ahí (p. ej.
la de BOPRIN-99977), cópialo al volumen **antes** de desplegar:

```
docker compose --env-file .env.production -f docker-compose.prod.yml -f docker-compose.plesk.yml \
  exec api sh -c 'mkdir -p /opt/crmbo/uploads/erp-shipping/_rescate && cp -a /app/uploads/erp/. /opt/crmbo/uploads/erp-shipping/_rescate/ 2>/dev/null; ls -R /opt/crmbo/uploads/erp-shipping/_rescate | head'
```

El traslado las recoge de ahí al arrancar el `api` nuevo. Después, la carpeta
`_rescate` se puede borrar.
