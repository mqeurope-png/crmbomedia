# Dominios compartidos: los que nunca emparejan (1/11/2026)

`xtec.cat` no identifica un instituto igual que `gmail.com` no identifica una
empresa: lo usan miles de docentes de cientos de centros. El emparejamiento por
dominio (Brevo y el relleno de empresas) trataba esos dominios como si
identificaran una empresa. El resultado fue miles de contactos pegados a la única
empresa con ese dominio, o a una «Centro público» creada a partir de él.

## La lista

**Administración → Dominios compartidos.** Es un dato, no código: añadir un
dominio no requiere desplegar.

- **Compartido (organización)**: consejerías de educación, administración
  pública… La migración 0166 siembra once:

  | | | |
  |---|---|---|
  | `xtec.cat` | `juntadeandalucia.es` | `edu.xunta.gal` |
  | `gobiernodecanarias.org` | `edu.jccm.es` | `educa.aragon.es` |
  | `educa.jcyl.es` | `educa.madrid.org` | `hezkuntza.net` |
  | `educaib.eu` | `educacion.navarra.es` | |

- **Proveedor de correo**: `gmail.com`, `hotmail.es`… Es la lista que antes vivía
  en el código (`PERSONAL_DOMAINS`), pasada a la tabla tal cual.

Un subdominio cuenta como su dominio: con `xtec.cat`, también
`alumnes.xtec.cat`.

### Qué hace la lista

- **Brevo**: un contacto con correo de la lista no se vincula por el dominio.
  - Una web de la lista no sirve para buscar ni para crear la empresa.
  - Si declara una «EMPRESA» sin CIF y con correo o web de un dominio compartido,
    se queda sin empresa. Inventarla, una nueva en cada sincronización, sería
    repetir el error.
  - Con CIF se sigue buscando por el CIF.
- **El relleno de empresas** (`backfill_companies_from_contacts`) aplica la
  misma regla.
- **Fusionar empresas**: la superviviente no hereda un dominio de la lista.
  Heredarlo la convertiría en el imán de todo el que lo use.
- **Lo que no hace**: no despega a quien ya está vinculado. Eso lo hace el
  script de abajo, con prueba antes.

## Diagnóstico (solo lee)

```bash
docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \
    python -m scripts.diagnostico_dominios [--top 60]
```

Lista los dominios por contactos y por clientes de FACTUSOL distintos. Marca como
sospechoso el que cumple alguna de estas:

- lo usan varios clientes de FACTUSOL;
- tiene en una sola empresa más de 3 veces el percentil 99 de contactos por
  empresa real;
- parece de administración o educación;
- es de un proveedor de correo.

Termina con:

- una **propuesta** de dominios que añadir, que decide Bart;
- cuánta de la etiqueta YA CLIENTE viene de contactos de dominios compartidos;
- la comparación «huecos por gama»: empresas de la pestaña frente a contactos
  de la regla.

Sin nombres de personas ni de empresas: las empresas salen por id corto y código
de cliente.

## Despegar lo que ya está pegado

```bash
# 1. Qué haría, por empresa. No escribe nada.
#    --csv deja en el servidor el listado con nombres.
... python -m scripts.despegar_dominios_compartidos --probar [--csv /tmp/despegue.csv]
# 2. Hacerlo. Recalcula después el resumen por empresa y las etiquetas.
... python -m scripts.despegar_dominios_compartidos --apply --yes
# Deshacer un lote entero.
... python -m scripts.despegar_dominios_compartidos --revertir LOTE --yes
# Ver los lotes.
... python -m scripts.despegar_dominios_compartidos --lotes
```

**Se despega** un contacto con empresa cuyo correo es de un dominio **compartido**
de la lista. Los proveedores de correo no entran: a `gmail.com` nunca se emparejó
por dominio, así que sus vínculos salen de otro sitio.

**Se queda** con su empresa si hay alguna prueba de que es de ella:

| Motivo | Prueba |
|---|---|
| `factusol` | es él mismo un cliente de FACTUSOL (`factusol_contact_id`) |
| `pedido` | figura en un pedido del ERP de esa empresa |
| `oferta` | figura en una oferta de BoHub de esa empresa |
| `a_mano` | alguien le puso esa empresa a mano: la ficha marca `company_id` como editado a mano, o la auditoría guarda la asignación (`assign-company`) |
| `declarada` | declaró en Brevo una empresa con el mismo nombre. Solo cuenta con `--conservar-declaradas`; apagado por defecto, porque «Centro público» lo declaran cientos de centros |

**Al despegar:**

- `company_id` pasa a nulo. El contacto ni se borra ni se desactiva.
- Queda un apunte por contacto en `crm_despegue_dominio` con la empresa anterior,
  el dominio y el motivo.
- **Empresas archivadas** (con `is_archived`, nunca borradas): las que se quedan
  sin contactos, no tienen cliente de FACTUSOL, tienen un dominio de la lista o
  ninguno, y no tienen pedidos, ofertas ni tareas.
- Una empresa con cliente de FACTUSOL no se archiva nunca. Conserva su vínculo,
  su parque y su histórico; solo pierde los contactos que no eran suyos.

**Revertir:** cada contacto vuelve a su empresa anterior y cada empresa archivada
se desarchiva. Si a alguien le han puesto ya otra empresa, o la anterior ya no
existe, no se toca y se cuenta.

**Después del `--apply`** el script:

- recalcula el resumen por empresa y las etiquetas. Los despegados pierden la
  YA CLIENTE automática; las puestas a mano se quedan;
- enseña YA CLIENTE antes y después;
- enseña los huecos por gama;
- enseña las vistas y segmentos que cambian más de un 20 % (y de 20 contactos).
  Los segmentos dinámicos de Brevo se recalculan solos en la siguiente
  sincronización.

## Lo que no hace

- No reasigna los despegados a su centro de verdad. No se puede saber por el
  correo, y adivinarlo sería repetir el error.
- No rehace el emparejamiento por nombre o NIF.
- No escribe en FACTUSOL.
