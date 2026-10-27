"""Lo que el redactor va a buscar FUERA del catálogo: una imagen por URL, el
soporte de terceros (FLUX…) y el índice de nuestras webs.

Tres piezas comunes, que usan las tres partes:

- `red`: las peticiones a la red de fuera, con cerrojos (solo HTTPS, nada de
  direcciones privadas ni de la red de Docker, tiempo y tamaño máximos,
  redirecciones revisadas una a una).
- `html_texto`: de una página HTML al texto que importa (fuera menú, pie y
  barras laterales; aviso si es una cáscara que se pinta con JavaScript).
- `material`: cómo entra lo descargado en el prompt — encapsulado como
  MATERIAL DE CONSULTA, que es dato y nunca manda sobre las instrucciones.

Nada de esto escribe fuera: solo lee, y lee por endpoints públicos.
"""
