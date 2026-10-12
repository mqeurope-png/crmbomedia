"""Correos compuestos: bloques de BoHub, su HTML de correo y su texto plano.

Un correo compuesto es una ESTRUCTURA (lista de bloques con sus productos,
textos e idioma). La IA la elige y escribe los textos; el HTML lo genera
`generador.generar`, código determinista, con tablas y estilos en línea para
que sobreviva a los clientes de correo. Los datos de producto (nombre,
imagen, precio tal cual, enlace por idioma) salen siempre del catálogo
(`kb_catalogo`), nunca de lo que escriba la IA.
"""
