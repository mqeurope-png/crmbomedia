# Los párrafos de los correos van en una línea cada uno a propósito: partirlos
# cambiaría el texto que recibe el cliente, y el cliente de correo ya los
# ajusta al ancho de su ventana.
# ruff: noqa: E501
"""Las seis plantillas del acuse de recibo, una por idioma.

Seis cubren las ocho webs porque la marca es una variable. Las crea la
migración `20261010_0131` como plantillas de correo normales, así que se
pueden editar desde Plantillas sin tocar código; esto es lo que se siembra.

Variables (dobles llaves, las resuelve `acuse.aplicar_variables`):

  `{{nombre}}`     el nombre de pila del contacto
  `{{marca}}`      el nombre comercial de la web (`sitios.MARCAS`)
  `{{web}}`        el dominio de la web, de la clave del slug
  `{{productos}}`  las etiquetas que marcó, por su nombre
  `{{consulta}}`   lo que escribió

Y los bloques `{{#productos}}…{{/productos}}` y `{{#consulta}}…{{/consulta}}`,
que desaparecen enteros cuando la variable está vacía: ni «Productos que te
interesan:» seguido de un hueco, ni un bloque de consulta vacío. Lo segundo
pasa de verdad — uno de los leads de prueba del 08/10 mandó la consulta en
blanco.
"""

from __future__ import annotations

#: Nombre de la carpeta donde viven, en Plantillas.
CARPETA = "Acuses de recibo (formularios web)"

#: `idioma → {nombre, asunto, html, texto}`.
PLANTILLAS: dict[str, dict[str, str]] = {
    "es": {
        "nombre": "Acuse de recibo · formulario web (español)",
        "asunto": "Hemos recibido tu consulta · {{marca}}",
        "html": """<p>Hola {{nombre}},</p>
<p>Gracias por escribirnos. Hemos recibido tu consulta a través de {{web}} y
ya está en manos de nuestro equipo.</p>
{{#productos}}<p><strong>Productos que te interesan:</strong> {{productos}}</p>{{/productos}}
{{#consulta}}<p><strong>Lo que nos cuentas:</strong><br>{{consulta}}</p>{{/consulta}}
<p>Te contestaremos a la mayor brevedad posible.</p>
<p>Un saludo,<br><strong>{{marca}}</strong><br>{{web}}</p>""",
        "texto": """Hola {{nombre}},

Gracias por escribirnos. Hemos recibido tu consulta a través de {{web}} y ya está en manos de nuestro equipo.
{{#productos}}
Productos que te interesan: {{productos}}
{{/productos}}{{#consulta}}
Lo que nos cuentas:
{{consulta}}
{{/consulta}}
Te contestaremos a la mayor brevedad posible.

Un saludo,
{{marca}}
{{web}}""",
    },
    "en": {
        "nombre": "Acuse de recibo · formulario web (inglés)",
        "asunto": "We've received your enquiry · {{marca}}",
        "html": """<p>Hello {{nombre}},</p>
<p>Thank you for getting in touch. We've received your enquiry through {{web}}
and it's already with our team.</p>
{{#productos}}<p><strong>Products you're interested in:</strong> {{productos}}</p>{{/productos}}
{{#consulta}}<p><strong>What you told us:</strong><br>{{consulta}}</p>{{/consulta}}
<p>We'll get back to you as soon as possible.</p>
<p>Kind regards,<br><strong>{{marca}}</strong><br>{{web}}</p>""",
        "texto": """Hello {{nombre}},

Thank you for getting in touch. We've received your enquiry through {{web}} and it's already with our team.
{{#productos}}
Products you're interested in: {{productos}}
{{/productos}}{{#consulta}}
What you told us:
{{consulta}}
{{/consulta}}
We'll get back to you as soon as possible.

Kind regards,
{{marca}}
{{web}}""",
    },
    "fr": {
        "nombre": "Acuse de recibo · formulario web (francés)",
        "asunto": "Nous avons bien reçu votre demande · {{marca}}",
        "html": """<p>Bonjour {{nombre}},</p>
<p>Merci de nous avoir écrit. Nous avons bien reçu votre demande via {{web}}
et elle est déjà entre les mains de notre équipe.</p>
{{#productos}}<p><strong>Produits qui vous intéressent :</strong> {{productos}}</p>{{/productos}}
{{#consulta}}<p><strong>Votre message :</strong><br>{{consulta}}</p>{{/consulta}}
<p>Nous vous répondrons dans les meilleurs délais.</p>
<p>Cordialement,<br><strong>{{marca}}</strong><br>{{web}}</p>""",
        "texto": """Bonjour {{nombre}},

Merci de nous avoir écrit. Nous avons bien reçu votre demande via {{web}} et elle est déjà entre les mains de notre équipe.
{{#productos}}
Produits qui vous intéressent : {{productos}}
{{/productos}}{{#consulta}}
Votre message :
{{consulta}}
{{/consulta}}
Nous vous répondrons dans les meilleurs délais.

Cordialement,
{{marca}}
{{web}}""",
    },
    "de": {
        "nombre": "Acuse de recibo · formulario web (alemán)",
        "asunto": "Wir haben Ihre Anfrage erhalten · {{marca}}",
        "html": """<p>Hallo {{nombre}},</p>
<p>vielen Dank für Ihre Nachricht. Wir haben Ihre Anfrage über {{web}}
erhalten, und sie liegt bereits bei unserem Team.</p>
{{#productos}}<p><strong>Produkte, die Sie interessieren:</strong> {{productos}}</p>{{/productos}}
{{#consulta}}<p><strong>Ihre Nachricht:</strong><br>{{consulta}}</p>{{/consulta}}
<p>Wir melden uns schnellstmöglich bei Ihnen.</p>
<p>Mit freundlichen Grüßen<br><strong>{{marca}}</strong><br>{{web}}</p>""",
        "texto": """Hallo {{nombre}},

vielen Dank für Ihre Nachricht. Wir haben Ihre Anfrage über {{web}} erhalten, und sie liegt bereits bei unserem Team.
{{#productos}}
Produkte, die Sie interessieren: {{productos}}
{{/productos}}{{#consulta}}
Ihre Nachricht:
{{consulta}}
{{/consulta}}
Wir melden uns schnellstmöglich bei Ihnen.

Mit freundlichen Grüßen
{{marca}}
{{web}}""",
    },
    "nl": {
        "nombre": "Acuse de recibo · formulario web (neerlandés)",
        "asunto": "We hebben uw vraag ontvangen · {{marca}}",
        "html": """<p>Hallo {{nombre}},</p>
<p>Bedankt voor uw bericht. We hebben uw vraag via {{web}} ontvangen en ons
team is er al mee bezig.</p>
{{#productos}}<p><strong>Producten waarin u geïnteresseerd bent:</strong> {{productos}}</p>{{/productos}}
{{#consulta}}<p><strong>Wat u ons schreef:</strong><br>{{consulta}}</p>{{/consulta}}
<p>We nemen zo spoedig mogelijk contact met u op.</p>
<p>Met vriendelijke groet,<br><strong>{{marca}}</strong><br>{{web}}</p>""",
        "texto": """Hallo {{nombre}},

Bedankt voor uw bericht. We hebben uw vraag via {{web}} ontvangen en ons team is er al mee bezig.
{{#productos}}
Producten waarin u geïnteresseerd bent: {{productos}}
{{/productos}}{{#consulta}}
Wat u ons schreef:
{{consulta}}
{{/consulta}}
We nemen zo spoedig mogelijk contact met u op.

Met vriendelijke groet,
{{marca}}
{{web}}""",
    },
    "pt": {
        "nombre": "Acuse de recibo · formulario web (portugués)",
        "asunto": "Recebemos a sua consulta · {{marca}}",
        "html": """<p>Olá {{nombre}},</p>
<p>Obrigado por nos ter escrito. Recebemos a sua consulta através de {{web}}
e já está nas mãos da nossa equipa.</p>
{{#productos}}<p><strong>Produtos do seu interesse:</strong> {{productos}}</p>{{/productos}}
{{#consulta}}<p><strong>O que nos escreveu:</strong><br>{{consulta}}</p>{{/consulta}}
<p>Responderemos com a maior brevidade possível.</p>
<p>Com os melhores cumprimentos,<br><strong>{{marca}}</strong><br>{{web}}</p>""",
        "texto": """Olá {{nombre}},

Obrigado por nos ter escrito. Recebemos a sua consulta através de {{web}} e já está nas mãos da nossa equipa.
{{#productos}}
Produtos do seu interesse: {{productos}}
{{/productos}}{{#consulta}}
O que nos escreveu:
{{consulta}}
{{/consulta}}
Responderemos com a maior brevidade possível.

Com os melhores cumprimentos,
{{marca}}
{{web}}""",
    },
}
