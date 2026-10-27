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
    (r"^test|-test$|test-form|testagile|sample-page|pagina-ejemplo", "páginas de prueba"),
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
)
