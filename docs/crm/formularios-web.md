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

## Un solo código de inserción por web (web + idioma)

Con 25 formularios en ocho webs y seis idiomas, pegar un código por
traducción son 23 pegadas. El embed puede resolverse **por web**:

```html
<script src="https://…/forms/embed/mbolasers.js" async></script>
<div data-bohub-form="mbolasers"></div>
```

- Las **dos líneas** son necesarias: sin el `<div>` el script carga y no
  pinta nada.
- El widget mira el idioma de la página: el `lang` del `<html>` y, si no lo
  trae, el primer trozo de la URL (`/de/kontakt`).
- La web **no** es la marca: `artisjet` es la marca de dos webs distintas. La
  web es la **clave del slug**, lo que va antes de `-contacto`
  (`artisjet-es-contacto-de` → `artisjet-es`), así que esas dos tienen su
  propio código: `/forms/embed/artisjet-es.js` y `/forms/embed/artisjet-eu.js`.
- Sirve el formulario de esa web en ese idioma. Si no lo hay: el marcado como
  **respaldo de la web** (casilla en el editor), y si tampoco, el castellano,
  el inglés o el primero por idioma.
- Una página **sin `lang`** —o en un idioma que no tenemos— no cuenta como
  página en castellano: también se lleva el respaldo. Por eso conviene marcar
  uno en cada web, normalmente el inglés en las de fuera de España.
- Si la web no tiene **ningún** formulario activo, el script lo dice en la
  consola del navegador (`site_without_forms`) y marca el `<div>`.
- El embed **por id sigue igual**: es lo que está pegado hoy.
- La pantalla de código ofrece los dos, con el `<div>` incluido.

## El formulario, en su idioma

`app/services/web_forms/textos.py` tiene, por idioma (es, en, fr, de, pt,
nl), el texto del botón y todo lo que ve quien rellena: el acuse, los
errores de envío, el aviso de que falta algo obligatorio. Lo que falte en un
idioma cae al castellano.

- El botón: el texto propio del formulario si lo tiene (Apariencia →
  «Texto del botón»); si no, el de su idioma (Enviar / Send / Envoyer /
  Senden / Versturen / Enviar).
- El acuse de recibo que se manda al lead (si está activado) también va en
  el idioma del formulario cuando no hay plantilla elegida.
- Los mensajes viajan en `config.json` (`texts`) y van inlineados en el
  iframe y en el fragmento: ya no hay castellano fijo en el JS.
- Los mensajes de validación del navegador (campo obligatorio, correo mal
  escrito) los pone el navegador en su propio idioma; no se pueden traducir
  desde aquí. El `<form>` lleva su `lang` para los navegadores que lo miran.

## Qué hace un envío con el contacto

- Crea o completa el contacto (solo rellena lo vacío), la empresa, el
  propietario y su aviso: como siempre.

### El modo de asignación tiene que ser uno de los tres

`assignment_mode` solo admite `rules` (las reglas de asignación),
`fixed_owner` (el comercial fijo del formulario, que entonces es obligatorio)
y `none` (se asigna a mano después).

Los 25 formularios se cargaron con `fixed` en vez de `fixed_owner`, y el
motor se lo encontraba, no lo reconocía y no hacía nada **sin decirlo**:
durante días ningún lead se asignó a nadie, el aviso no tenía persona a quien
ir y los leads no salían en la cartera de ningún comercial. Ahora:

- la pantalla y la API **rechazan** un valor que el motor no entienda, con un
  error que dice los válidos, y el modelo tampoco lo deja guardar por el ORM;
- si aun así hay uno mal en la base de datos, el envío se procesa y el lead se
  guarda, pero queda un aviso en el log identificando el formulario
  (`web_forms.asignacion`). Lo mismo si es `fixed_owner` sin comercial puesto;
- la migración 0128 repara los que se puedan deducir (`fixed` →
  `fixed_owner`) y deja en el log los que no;
- el Cuadre lista en **«Lead web sin comercial asignado»** (media) los leads
  de formulario de los últimos días que se quedaron sin nadie.
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

## De dónde viene el lead

- `contacts.origin` → «Formulario web · mboprinters.com (alemán)»: es lo que
  se lee en **Origen del lead** de la ficha, sin traducir códigos.
- `contacts.origin_account_id` → `web_form:<sitio>:<idioma>`.
- `contacts.language` → el idioma del formulario, **columna propia**: no se
  queda dentro del texto del origen, así que se filtra, se segmenta y se
  ordena por él cruzando webs («todos los leads en alemán», sean de
  mboprinters, artisjet-printers.eu o mbolasers). También se copia a la
  empresa si la tiene vacía: es de donde lee el idioma la cascada de los
  correos y los PDF del ERP, así que quien escribió en neerlandés no recibe
  después un correo en castellano. Un idioma ya puesto nunca se pisa.
- En los filtros de Contactos y en los segmentos: **Web del formulario**
  (desde el origen) e **Idioma del contacto** (la columna). Así se listan
  «los leads de mboprinters» o «los leads en alemán» sin tirar de la
  etiqueta `form:<slug>` (que sigue existiendo).
- La web sale de la clave del slug, nunca de la marca (`artisjet` está en dos
  webs). La lista está en `app/services/web_forms/sitios.py` (`WEBS`):

  | Slug | Web |
  |---|---|
  | `artisjet-es-contacto-*` | artisjet-spain.es |
  | `artisjet-eu-contacto-*` | artisjet-printers.eu |
  | `boprint-contacto` | boprint.net |
  | `fluxlasers-contacto-*` | fluxlasers.es |
  | `mboprinters-contacto-*` | mboprinters.com |
  | `mbolasers-contacto-*` | mbolasers.com |
  | `mqeurope-contacto-*` | www.mqeurope.com (sin www redirige) |
  | `pimpam-contacto-*` | pimpam-vending.com |

  Un sitio que no esté se enseña con su clave, nunca con un dominio
  inventado. Al publicar una web nueva, su dominio se escribe ahí y aparece
  en la ficha, en los filtros y en los avisos.
- **El nombre del slug importa.** Un formulario cuyo slug no lleve
  `<web>-contacto…` es su propio sitio: funciona y captura leads, pero se
  queda fuera del código único de su web, del filtro por web y del dominio
  en la ficha y en el aviso. El editor lo avisa debajo del campo.
- Un contacto que **ya existía** conserva su origen: el de un formulario
  solo se pone al crearlo (como el resto de orígenes).
- La migración 0127 reconstruyó los leads ya entrados (tenían
  `web_form:<slug>`), por el slug del formulario o, si no cuadra, por el
  envío guardado.

## Aviso por correo de cada lead

Lo gobierna el interruptor que ya existía, **«Notificar al owner de cada
lead nuevo»**: apagarlo silencia el aviso entero.

- **Asunto**: «Nuevo lead desde mboprinters.com (alemán)».
- **Cuerpo**: nombre y apellidos, correo, teléfono, las etiquetas que marcó
  (por su nombre, no el uuid), el resto de campos que rellenó, si aceptó
  comunicaciones comerciales, su consulta entera y un enlace a la ficha.
- **Destinatarios**: uno, no dos.
  - Si el lead **tiene comercial** (la regla de asignación o el propietario
    fijo), el aviso va **solo a él**. Los comerciales comparten o reenvían el
    buzón genérico, así que mandarlo a los dos hacía que cada lead llegara
    dos veces y no lo leyera nadie.
  - Si **no tiene** comercial, va a las direcciones fijas de
    `WEB_FORMS_NOTIFY_TO` (`info@streamtec.es` por defecto, separadas por
    comas). Es el caso en el que hace falta que alguien lo vea, así que nunca
    se queda sin aviso; también pasa si el comercial no tiene correo puesto o
    **está dado de baja** (dar de baja a alguien no le quita sus leads, y su
    buzón ya no lo lee nadie).
  - `WEB_FORMS_NOTIFY_ALWAYS=true` vuelve al comportamiento de antes (los dos
    siempre), por si el reparto de buzones cambia.
- **Variante por web sin tocar código**: si existe
  `app/templates/email/lead_<sitio>.html` (y/o `.txt`) se usa esa en lugar
  de `lead_notification.html`, así una web puede llevar su logotipo y su
  firma.
- Un fallo de correo **no tumba** la captura: el lead se guarda igual y el
  fallo queda en el log (`web_forms.aviso_lead`).

## Acuse de recibo al lead

El correo que recibe **quien rellena el formulario**. Lo gobierna la casilla
**«Enviar email de confirmación al lead»**.

- **Sale de la web por la que entró el lead**, no de la aplicación: un cliente
  que escribe a mboprinters.com recibe la respuesta de `info@mboprinters.com`.
  Es un campo del formulario, **«Remitente del acuse»**, y si se deja vacío se
  usa el de su web:

  | Web | De |
  |---|---|
  | boprint.net | `info@boprint.net` |
  | artisjet-spain.es | `info@artisjet-spain.es` |
  | artisjet-printers.eu | `info@artisjet-printers.eu` |
  | mboprinters.com | `info@mboprinters.com` |
  | mbolasers.com | `info@mbolasers.com` |
  | fluxlasers.es | `info@fluxlasers.es` |
  | mqeurope.com | `sales@mqeurope.com` (en ese dominio no hay `info@`) |
  | pimpam-vending.com | `info@pimpam-vending.com` |

  Una web que no esté en la tabla y sin remitente puesto a mano **no manda
  nada**: inventarse un `info@` de un dominio que no firma el correo lo
  mandaría a la carpeta de spam.

- **`Reply-To`: el comercial del lead.** El cliente ve la marca, pero si
  contesta le llega a una persona. Sin comercial —o con el comercial dado de
  baja—, el `Reply-To` es el propio remitente.

- **Va por el mismo camino que la Bandeja** (Gmail, con el alias de la marca),
  así que **no es un correo fantasma**: aparece en Enviados, en el histórico
  de correos de la ficha del contacto y con el seguimiento de apertura de
  siempre. Por eso los ocho remitentes tienen que estar como **alias de envío**
  en la cuenta de Google de la organización; si un alias no está
  sincronizado, el acuse **no se manda** y queda en el log con el alias que
  falta (Gmail reescribiría el remitente, que es justo lo que se quiere
  evitar).

- **Plantilla por idioma**, en Plantillas → «Acuses de recibo (formularios
  web)». Seis cubren las ocho webs porque la marca es una variable:

  | Variable | Qué es |
  |---|---|
  | `{{nombre}}` | nombre de pila del contacto |
  | `{{marca}}` | nombre comercial de la web (Artisjet Europe, MBO Printers…) |
  | `{{web}}` | dominio de la web |
  | `{{productos}}` | las etiquetas que marcó, por su nombre |
  | `{{consulta}}` | lo que escribió |

  Y bloques `{{#productos}}…{{/productos}}` / `{{#consulta}}…{{/consulta}}`,
  que **desaparecen enteros** cuando la variable está vacía: ni «Productos que
  te interesan:» seguido de un hueco, ni un bloque de consulta en blanco.

- Un fallo **no tumba** la captura: el lead se guarda igual y el fallo queda en
  el log (`web_forms.acuse`).

## Textos con enlaces

En la etiqueta y la ayuda de un campo:

- `[texto](https://…)` o `[texto](/ruta/)` salen como enlace (nueva pestaña,
  `rel="noopener noreferrer"`) en las tres vías.
- Una ruta que empieza por `/` vale en la web de cada marca sin escribir el
  dominio: `[Política de privacidad](/politica-de-privacidad/)`.
  - Con el script JS y el HTML puro el navegador la resuelve contra la web.
  - El iframe se sirve desde BoHub: su script la reescribe contra el origen de
    la página que lo inserta (`ancestorOrigins` o `document.referrer`).
- Una casilla mapeada al consentimiento no admite «valor por defecto»: solo
  cuenta si la persona la marca.
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
