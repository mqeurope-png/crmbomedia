"""Las reglas de partida del índice de las webs: las siembra la migración 0148
y valen mientras la tabla `web_indice_reglas` esté vacía. Lo que se cambie de
verdad se cambia en la tabla (Configuración ERP → Base de conocimiento).

- `pagina_excluir_slug`: expresiones regulares (sin distinguir mayúsculas)
  sobre el slug de páginas y entradas: legales, carrito, pago, cuenta, avisos
  de «enviado» y páginas de prueba, en los idiomas de las webs (medido el
  27/10/2026 en las siete webs a las que se llega).
- `categoria_fuera`: un producto queda fuera si alguna de sus categorías, o
  alguna madre de ellas, EMPIEZA por uno de estos nombres (sin distinguir
  mayúsculas ni tildes). Los productos sin categoría también quedan fuera.
- `producto_excluir_id` / `producto_incluir_id`: excepciones por id, cada una
  de su web. Son los productos que la categoría no separa bien: artisjet-spain.es
  mete cabezales y dampers en «Accesorios» y artisjet-printers.eu mezcla
  consumibles en «Accessoires et pièces de rechange» (repasado a mano sobre el
  recorrido real del 27/10/2026).
"""
from __future__ import annotations

#: (patrón, nota)
PAGINAS_FUERA: tuple[tuple[str, str], ...] = (
    (r"cooki", "cookies"),
    (r"privac|datenschutz|proteccion-datos|confidentialit", "privacidad"),
    (r"aviso-legal|legal-notice|legal-warning|mentions-legales|impressum|juridisch"
     r"|rechtliche|avis-juridique", "aviso legal"),
    (r"condicion|condition|terms|voorwaarden|^agb$|contratacion|contratacao"
     r"|clausulas-informativas", "condiciones y términos"),
    (r"devoluci|^returns?$|retours?|widerruf|refund", "devoluciones"),
    (r"^(carrito|cart|basket|panier|einkaufswagen|winkelkarretje|winkelwagen)$", "carrito"),
    (r"^(checkout|finalizar-compra|pago|pasarela-de-pago|zur-kasse|paiement|afronden"
     r"|compra-finalizada|orders)$", "pago"),
    (r"^(mi-cuenta|my-account|mein-account|mon-compte|mijn-account|account)$", "mi cuenta"),
    (r"^(enviado|sent|gesendet|envoye|verstuurd)$", "aviso de formulario enviado"),
    # `^test` sin «testimonios», «testimonials», «testimonianze» ni «testemunhos».
    (r"^test(?!imon|emun)|-test$|test-form|testagile|sample-page|pagina-ejemplo",
     "páginas de prueba"),
    (r"^dpd-|^app-posts|^app-crea|cowork", "páginas internas"),
    (r"accesibilidad|accessibility", "declaración de accesibilidad"),
)

#: (prefijo, nota)
CATEGORIAS_FUERA: tuple[tuple[str, str], ...] = (
    ("Repuestos", ""),
    ("Recambios", ""),
    ("Pièces de rechange", ""),
    ("Consumibles", ""),
    ("Tintas", "incluye «Tintas Ador», «Tintas UV Led…»"),
    ("Encres", "tintas en francés (artisjet-printers.eu)"),
    ("Cartuchos", ""),
    ("Packs de servicio", ""),
    ("Services", ""),
    ("Servicios", ""),
    ("Reacondicionadas", ""),
    ("Autres pièces de rechange", "otros recambios en francés (artisjet-printers.eu)"),
)

#: (web, id de producto, nota): fuera aunque su categoría sea de dentro.
PRODUCTOS_FUERA: tuple[tuple[str, int, str], ...] = (
    ("artisjet-es", 470, "Cabezal Epson DX5: recambio en «Accesorios»"),
    ("artisjet-es", 474, "Cabezal Epson L801: recambio en «Accesorios»"),
    ("artisjet-es", 481, "Damper Textil y EcoS: recambio en «Accesorios»"),
    ("artisjet-es", 485, "Damper UV Led: recambio en «Accesorios»"),
    ("artisjet-es", 2026051305, "artisJet DIVE (copia): producto duplicado"),
    ("artisjet-eu", 1892, "Polyester cleaning wipes: consumible"),
    ("artisjet-eu", 1989, "Syringe Kit: consumible"),
    ("artisjet-eu", 2979, "Hojas y films UV DTF: consumible"),
)

#: (web, id de producto, nota): dentro aunque su categoría sea de fuera.
PRODUCTOS_DENTRO: tuple[tuple[str, int, str], ...] = (
    ("artisjet-eu", 477, "Plantillas y moldes a medida: accesorio en «Autres pièces de "
                         "rechange»"),
)
