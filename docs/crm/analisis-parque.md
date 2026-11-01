# Análisis del parque (IA)

Desde el 1/11/2026, la ficha de empresa tiene un **análisis del parque** pensado
para una llamada o un correo. La ficha de contacto enseña un resumen.

## Cómo está hecho

Lo que se puede **calcular** no lo escribe la IA. BoHub calcula los datos del
parque y del histórico de esa empresa, y cada uno lleva su **factura, su albarán
o su fecha**, enlazados a la ficha del documento:

| Bloque | Qué dice |
|---|---|
| **Qué tiene** | Las máquinas en servicio, con su gama, su fecha y su antigüedad. |
| **Qué consume** | Qué compra de la gama de esas máquinas, cuántas veces, la última y cada cuánto (la mediana entre compras). |
| **Qué no compra (o ha dejado de comprar)** | Lo que se le puede proponer, ordenado por **encaje** con ese cliente (ver abajo). |
| **Qué ha cambiado** | «Compraba cada N meses y lleva M sin pedir». |
| **Lo que no se sabe** | Una máquina de más de N años que nadie ha dado de baja (puede estar en un contenedor), una sin gama, consumible de una gama sin máquina… |

**La IA solo escribe dos cosas:** un resumen de dos o tres frases y **«por dónde
entrar»**. Cada frase tiene que citar los datos en los que se apoya (D1, D2…),
y la cita es un enlace al dato.

- **Lo que no cita nada, o cita un dato que no existe, se descarta.** Abajo se
  dice cuántas frases se tiraron.
- **«Por dónde entrar» también tiene que citar** un dato calculado (un hueco, un
  cambio, una máquina, una compra), no un aviso. Si no puede apoyarse en
  ninguno, no se escribe: «Sin propuesta».
- **Producto:** solo se puede proponer uno de la lista de «qué no compra», que
  va ordenada por **encaje** con ese cliente, no por lo más vendido (1/11/2026):
  1. **Lo que le compraba y ha dejado de comprar**: al menos dos compras, lleva
     sin pedirlo el doble de su ritmo (y al menos 6 meses), y se sigue
     vendiendo.
  2. **Lo que nunca ha comprado, pero compran clientes como él.** Si compra
     consumibles de esa gama, «como él» son los que compran sus mismos
     consumibles; si no, los que tienen su mismo modelo de máquina. Hace falta
     que lo compren al menos 2 de ellos y al menos el 20 %.
  3. **El volumen de ventas** solo desempata.

  Si los datos no dicen qué le encaja (ningún cliente como él lo compra), no se
  supone la compatibilidad: se dice en «lo que no se sabe» y no se propone.
  - Esa lista son artículos de FACTUSOL de clase consumible, con gama de verdad
    (no «Sin gama») y de la gama de sus máquinas.
  - Además, vendidos a algún cliente en los últimos N meses, para no ofrecer lo
    descatalogado.
  - Una frase que nombra un artículo que no es suyo ni de la lista se descarta.

**Sin cliente de FACTUSOL vinculado** no hay análisis, y se dice: no se escribe
uno de la nada. Tampoco lo hay sin máquinas ni compras de consumible, ni en una
empresa marcada «no es cliente final».

## «No es cliente final»

En la ficha de empresa hay una casilla para la financiera del leasing, un
revendedor o nosotros mismos. Marcada:

- sus máquinas se siguen viendo en su ficha, pero **no cuentan en el parque, ni
  en dormidos y huecos**, ni en las reglas de filtro del parque;
- sus contactos **no entran en las campañas**: segmentos dinámicos y listas de
  Brevo;
- su análisis no se hace, y no cuenta como «cliente como él» para otros.

No se borra nada, y se deshace desmarcándola. La marca la pone quien puede
editar la empresa, queda en la auditoría y recalcula el resumen por empresa.

## Cuándo se calcula

No se calcula para todas las empresas.

- **Al abrir la ficha de empresa** se genera si no lo había, y se guarda.
- **Se vuelve a generar** si los datos de esa empresa han cambiado desde
  entonces (una compra nueva, una máquina de baja, una reclasificación) o si el
  guardado tiene más de los meses configurados. También con «Volver a generar».
- **Desde la lista de Empresas:** se seleccionan y se pulsa «Generar el análisis
  del parque». Se hace en segundo plano, como mucho 50 de una vez, y salta las
  que están al día o no tienen datos.
- **La ficha de contacto** no genera nada. Enseña el de su empresa, dice que es
  de su empresa y no del contacto, y enlaza al entero.
- Se ve cuándo se calculó, con qué modelo y lo que costó.

## Qué sale hacia el modelo

A la API de Anthropic, la misma que usa el redactor, van:

- **Los textos de los datos calculados:** descripciones y códigos de artículo,
  gamas, fechas y cuántas veces.
- **La lista de productos** que se pueden proponer.

**No van** nombres de personas ni de la empresa, NIF, correos, direcciones, el
código de cliente ni los números de documento. El enlace al documento lo pone el
servidor a partir del id del dato.

## Configuración (Configuración ERP → «Análisis del parque (IA)»)

- **Modelo que lo escribe.** Se cambia aquí, sin desplegar. La lista es la del
  asistente.
- **Cada cuántos meses se rehace** aunque no cambien los datos.
- **Desde cuántos años** una máquina sin baja sale como «no se sabe si sigue en
  servicio».
- **Cuántos meses de ventas** cuentan para el catálogo que se puede proponer.
- **Solo administradores**, mientras se prueba.

## Comparar modelos

Para elegir modelo con datos, `scripts/comparar_analisis_parque.py` pasa los
mismos datos calculados por varios modelos (por defecto Sonnet 5.5 y Haiku 5.5)
y enseña lo que escribe cada uno y lo que costó. No guarda el análisis.

```
… exec api python -m scripts.comparar_analisis_parque \
    --cliente 1234 --cliente 5678 --cliente 9012 [--salida /tmp/comparar.md]
```

## Lo que no hace

- **No escribe el correo**: eso es del redactor.
- **No manda nada.**
- **No escribe en FACTUSOL.**
- **No hace reposición ni previsiones** (cada cuánto toca, cuándo llamar). Eso,
  si este análisis sirve, es lo siguiente.
