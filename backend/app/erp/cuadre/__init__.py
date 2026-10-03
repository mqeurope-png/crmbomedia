"""ERP · Cuadre — panel de descuadres entre BoHub, FACTUSOL, Genei y la hoja de
Drive (SOLO LECTURA: no escribe en FACTUSOL, no corrige datos, no mueve
estados; cada descuadre enlaza a la pantalla donde ya se arregla).

- `registry`: registro de comprobaciones (`@comprobacion`) y `Hallazgo`.
- `checks_mysql` / `checks_factusol`: las comprobaciones, por fuente.
- `engine`: corre las comprobaciones y guarda los descuadres (idempotente).
- `config`: Configuración ERP → «Cuadre» (activar, umbrales, hora).
- `job`: job nocturno en worker-sync y «Comprobar ahora».

Guía: `docs/erp/cuadre.md`.
"""
