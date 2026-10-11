"""El asistente de respuesta «Ayúdame a contestar».

Lo pide una persona, lo revisa una persona, lo envía una persona. Aquí se
monta el contexto (el hilo, la ficha, los pedidos, la base de conocimiento),
se le pide al modelo un BORRADOR y se guarda con su coste. No hay en este
paquete ningún camino que envíe correo: el envío es el del compositor, y
entonces se anota si el borrador salió tal cual, editado o se descartó.
"""
