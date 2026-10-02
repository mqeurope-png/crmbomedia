# CLAUDE.md

## Revisión de los PR

- **Una ronda de revisión por PR.** Dos solo si el PR escribe en FACTUSOL o
  migra datos. **Nunca una tercera.**
- Si tras la última ronda permitida quedan hallazgos de severidad **baja o
  media**, se anotan en el PR en una sección «Pendientes» y se fusiona.
- Para la hoja de Drive, la red de seguridad real es la **vista previa** antes
  de escribir y el **historial de versiones** de la hoja. No hace falta blindar
  la pestaña contra manipulaciones manuales improbables.

## Nivel de comprobación según el tamaño del cambio

Antes de empezar, clasifica el cambio y dilo en una línea:

- PEQUEÑO: solo pantalla, textos, enlaces, filtros, pestañas, mensajes; sin
  migraciones, sin escribir en FACTUSOL, sin tocar datos ni el espejo de Drive.
  Procedimiento: tests de los ficheros tocados + tsc + eslint/ruff solo sobre lo
  tocado; NADA de suite completa en local (la pasa el CI); sin ronda de revisión
  con agentes (una lectura tuya del diff, cinco minutos); sin suscripciones ni
  recordatorios; sin tocar guías salvo que cambie algo que el usuario ve; un
  commit, PR y «cym» en cuanto el CI esté en verde. Objetivo: menos de una hora
  de trabajo.
- NORMAL: lógica de negocio en backend o frontend sin escribir en FACTUSOL ni
  migrar datos. Tests de las áreas tocadas, jest completo si tocas frontend; una
  ronda de revisión; CI; cym.
- DELICADO: escribe en FACTUSOL, migra o corrige datos, toca el espejo de Drive,
  cobros o emisión de facturas. Como hasta ahora: hasta dos rondas, scripts
  supervisados con --probar / --apply.

En todos los niveles: no repitas en local lo que el CI va a ejecutar igual; no
esperes al CI narrando, abre el PR y fusiona cuando esté en verde; agrupa los
informes en un solo mensaje al final.
