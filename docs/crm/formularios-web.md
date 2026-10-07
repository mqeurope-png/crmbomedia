# Formularios web

Pantalla: **Formularios** en el menú (admin y manager), `/admin/forms`.
Código: `backend/app/api/web_forms_*.py`, `backend/app/services/web_forms/`,
`frontend/src/app/components/web-forms/`.

Tres formas de insertar un formulario (pantalla «Código de embed»):

- **Script JS** (`/forms/embed/{id}.js`): lo pinta en la web desde
  `/public/forms/{id}/config.json`.
- **iframe** (`/forms/{id}`): página propia. Avisa de su altura a la web
  (`postMessage`) y el código de inserción la aplica, así que crece con el
  contenido (errores de validación incluidos) en vez de recortarlo. Los iframes
  pegados antes siguen funcionando con su altura fija.
- **HTML puro**: fragmento sin estilos, para maquetar con el CSS del sitio.

## Qué hace un envío con el contacto

- Crea o completa el contacto (solo rellena lo vacío), la empresa, el
  propietario y su aviso: como siempre.
- **Campo de etiquetas (`tags`)**: las etiquetas marcadas se aplican al contacto
  (pestaña Etiquetas y columna antigua `contacts.tags`).
  - Solo valen las opciones del campo: nadie puede colar otra etiqueta desde
    fuera.
  - Una opción se reconoce por su `tag_id`, su valor o su texto. Si su etiqueta
    se borró y se volvió a crear, se encuentra por el nombre.
  - Un campo de etiquetas **no se mapea** a un campo del contacto: el editor lo
    impide y la migración 0126 dejó «sin mapeo» los que lo tenían (el `maquina`
    de PimPam estaba mapeado a `contact.first_name`).
- **Consentimiento comercial** (`contact.marketing_consent`): mapeable solo
  desde una casilla. Marcada → el contacto pasa a «granted» (queda en
  auditoría); sin marcar no se toca nada.

## Textos con enlaces

En la etiqueta y la ayuda de un campo:

- `[texto](https://…)` o `[texto](/ruta/)` salen como enlace (nueva pestaña,
  `rel="noopener noreferrer"`) en las tres vías.
- Una ruta que empieza por `/` vale en la web de cada marca sin escribir el
  dominio: `[Política de privacidad](/politica-de-privacidad/)`.
- Cualquier otra cosa (`javascript:`, `//otro-dominio`, HTML) se escapa como
  siempre.

## Activo / desactivado

- Desactivado no se muestra en la web.
- Las vías públicas lo dicen con su propio error (`403`, código
  `form_inactive`), distinto de «no existe» (`404 Form not found`).
- El script JS escribe el motivo en la consola del navegador.
- La pantalla de código avisa al copiar el código de un formulario
  desactivado.
- Se activa en el editor, casilla «Activo».

## Envíos y bloqueados

- La lista separa **envíos** (reales) de **bloqueados** (reCAPTCHA, honeypot,
  límite por IP…).
- Los bloqueados **sin datos** (bots que golpean la URL sin pasar por ninguna
  página: payload `{}`) se borran a los 90 días. Se hace al registrar un
  bloqueo nuevo.
- Los que traen datos se guardan para poder revisarlos.

## Apariencia

Bloque «Apariencia» del editor. Se guarda en `web_forms.appearance_json`.

- **Ancho**: % (25-100) con un máximo opcional en px.
- **Alineación**: izquierda, centrado o derecha.
- En el móvil (menos de 600 px) ocupa siempre el 100 %.
- **Tema**: claro, oscuro o «heredar de la web».
  - «Heredar» no impone tipografía ni fondo.
  - En el iframe solo hace transparente el fondo: un iframe no puede heredar la
    letra de la web.
- **Color principal** (botón y foco), **color del texto** y **color de fondo**
  del bloque.
- **Radio de borde**, **tipografía** (heredada o una lista corta) y **tamaño
  de letra**.
- **Texto del botón** (por defecto «Enviar»).
- Cada opción vacía deja el aspecto de siempre. Un formulario sin apariencia
  genera el mismo HTML que antes (test con la «foto» de
  `tests/fixtures/web_forms_golden`).
- El servidor valida todo: colores `#rrggbb`, porcentajes y tamaños en rango,
  opciones de una lista. El CSS se compone solo con esos valores; ningún texto
  del usuario llega al CSS.

**Vista previa**: el editor la pide al servidor
(`POST /api/admin/forms/preview`, sin guardar). Se pinta con el mismo código
que la web y se puede ver en tamaño escritorio o móvil.
