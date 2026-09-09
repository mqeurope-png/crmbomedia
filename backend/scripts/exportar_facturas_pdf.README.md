# exportar_facturas_pdf.ps1 — descargar PDF de facturas en lote

Descarga en lote los **PDF de facturas de FACTUSOL** (para archivar o adjuntar a
mano), **por serie+número**. Conduce el motor de PDF que BoHub ya tiene (el mismo
E4 que usa la descarga individual y el adjunto del envío F-1); **no reimplementa
nada**. **Solo lectura**: no envía, no marca, no escribe en FACTUSOL.

Funciona por **serie+número aunque la factura no esté enlazada a ningún pedido**
de BoHub (p. ej. las facturas de flux hechas a mano): el PDF se genera de los
datos de FACTUSOL, no del pedido. La **identidad fiscal** (empresa emisora) la
pone la **serie** (1 Bomedia, 2 MQ Europe, 5 Streamtec).

## Parte 1 — de dónde sale el PDF (contrato)

No hace falta ningún endpoint nuevo para descargar: **ya existe** un endpoint de
solo lectura que genera el PDF con el motor E4:

```
GET /api/erp/factusol/documents/facturas/{serie}/{codigo}/pdf?lang={es|en|de|fr|nl}
→ 200 application/pdf   (Content-Disposition: attachment; filename="Factura_{serie}-{codigo}_{cliente}.pdf")
→ 404 si esa factura no existe en FACTUSOL
```

- Sin efectos secundarios: no marca, no envía, no escribe en FACTUSOL.
- Funciona por serie+número **aunque no haya pedido enlazado**.
- `lang` cambia solo las etiquetas del PDF, nunca los datos; por defecto `es`.

Para **descarga múltiple** hay además un endpoint que empaqueta varias en un ZIP
(lo usan los botones de la interfaz; el script baja una a una):

```
POST /api/erp/factusol/documents/facturas/pdf-zip
     body: { "items": [ { "serie": 1, "codigo": 260742 }, ... ], "lang": "es"? }
→ 200 application/zip   (un PDF por factura; las que no existen van en _no_encontradas.txt)
→ 404 si NINGUNA de las seleccionadas existe
```

## Uso

Requisitos: Windows PowerShell 5.1+ (o PowerShell 7). Un usuario de BoHub **sin
2FA** con acceso ERP (rol admin/pedidos/user). El script pide email y contraseña
de forma segura (no quedan en el historial).

### Por CSV (`serie,codigo`)

```powershell
.\exportar_facturas_pdf.ps1 -BaseUrl https://tu-dominio -Csv .\facturas.csv
```

CSV (cabecera obligatoria `serie,codigo`, número desnudo):

```
serie,codigo
1,260738
1,260739
5,260086
```

### Por rango

```powershell
.\exportar_facturas_pdf.ps1 -BaseUrl https://tu-dominio -Serie 2 -Desde 526071 -Hasta 526096
```

### Parámetros

| Parámetro        | Por defecto            | Qué hace                                             |
|------------------|------------------------|-----------------------------------------------------|
| `-BaseUrl`       | (se pregunta)          | URL base de BoHub (`https://…`).                     |
| `-Csv`           | —                      | CSV con columnas `serie,codigo`.                     |
| `-Serie/-Desde/-Hasta` | —                | Rango alternativo al CSV (una serie, códigos A..B).  |
| `-Idioma`        | `es`                   | Idioma del PDF: `es\|en\|de\|fr\|nl`.                |
| `-OutDir`        | `.\facturas_pdf`       | Carpeta de salida (se crea si no existe).            |
| `-PausaSegundos` | `1`                    | Pausa entre descargas.                               |
| `-LogFile`       | `.\exportar_facturas_*.log` | Transcript de la ejecución.                    |

Cada PDF se guarda con el nombre legible que compone el backend
(`Factura_{serie}-{codigo}_{cliente}.pdf`). Al final imprime un resumen
(descargadas / fallidas o inexistentes) y deja un log. Las facturas que no
existen se **saltan** con aviso; el script no se detiene por ellas.

## Uso inmediato (las 7 de flux/Marta)

Con este CSV salen los 7 PDF de una vez:

```
serie,codigo
1,260738
1,260739
1,260740
1,260741
1,260742
1,260743
5,260086
```
