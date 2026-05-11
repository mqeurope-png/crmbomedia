# Backups y restauración

Estrategia de protección de datos del CRMBO Media CRM en producción.
Combina dos capas: snapshots locales gestionados por Plesk y backups
off-site cifrados con `restic` hacia IONOS HiDrive vía `rclone`/WebDAV.

## Estado actual (capa 1: Plesk)

* Plesk realiza backups semanales del servidor completo (incluyendo el
  volumen Docker del MySQL).
* Cubre: corrupción de fichero, error humano detectado dentro del retain
  configurado en Plesk, rollback rápido tras un mal deploy.
* **No cubre**:
  - Fallo total del disco del VPS (no se puede acceder a Plesk para
    restaurar).
  - Suspensión de la cuenta IONOS (los snapshots viven en la misma
    cuenta).
  - Intrusión que cifre / borre el disco (ransomware).
  - Error humano pre-restore que sobrescriba el snapshot local.

Por eso este repo añade una **segunda capa**: backups cifrados subidos
fuera del VPS, en una cuenta separada de almacenamiento (IONOS HiDrive),
con cifrado en cliente para que ni IONOS pueda leerlos.

## Backups off-site con restic + HiDrive

### Arquitectura

```
cron @03:00 UTC
   ↓
scripts/backup-mysql-restic.sh
   ↓
mysqldump --single-transaction        (dentro del contenedor db)
   ↓
gzip --best  →  /tmp/crmbo-<db>-<ts>.sql.gz
   ↓
restic backup --tag daily             (snapshot deduplicado + cifrado)
   ↓
rclone (WebDAV)
   ↓
https://webdav.hidrive.strato.com/users/<HIDRIVE_USER>/<HIDRIVE_PATH>/
```

### Por qué cifrado en cliente

* `restic` cifra **todos** los blobs con AES-256-CTR + Poly1305 antes de
  subirlos.
* IONOS HiDrive solo ve bytes pseudo-aleatorios; no puede leer ni
  indexar el contenido.
* Si la `RESTIC_PASSWORD` se pierde y no hay copia, los backups quedan
  **irrecuperables**. Trátala al mismo nivel que la contraseña root del
  VPS.

### Setup inicial (una sola vez en el VPS)

```bash
cd /opt/crmbo
sudo bash scripts/setup-restic-hidrive.sh
```

El script:

1. Comprueba que se ejecuta como root.
2. Instala `rclone` y `restic` (vía `dnf`+EPEL en AlmaLinux 8 / Rocky;
   `apt-get` en Debian / Ubuntu; binario upstream como último recurso).
3. Pregunta interactivamente por `HIDRIVE_USER`, `HIDRIVE_PASS`,
   `HIDRIVE_PATH` (default `bocrm`), `RESTIC_PASSWORD` y un webhook
   opcional para notificar fallos.
4. Escribe `/root/.config/rclone/rclone.conf` con un remote `[hidrive]`
   tipo WebDAV apuntando a HiDrive, con la pass ofuscada por
   `rclone obscure`.
5. Escribe `/etc/crmbo/backup.env` (perms `600`, root) con
   `RESTIC_REPOSITORY=rclone:hidrive:`, `RESTIC_PASSWORD=...` y el
   webhook opcional.
6. Valida la conexión: `rclone lsd hidrive:`.
7. Inicializa el repositorio restic la primera vez, o lo verifica con
   `restic snapshots` si ya existe (idempotente).
8. Instala `/etc/cron.d/crmbo-backup`:
   * **Diario 03:00 UTC** → `backup-mysql-restic.sh`.
   * **Mensual día 1 a las 04:00 UTC** → `restic check` (integridad
     estructural, no descarga blobs).
9. Imprime el resumen y los comandos de verificación manual.

### Variables a guardar en gestor de contraseñas

Sin estas tres, los backups son inútiles:

* **`RESTIC_PASSWORD`** — clave de cifrado del repositorio.
* **`HIDRIVE_USER`** — usuario IONOS HiDrive (email login).
* **`HIDRIVE_PASS`** — contraseña HiDrive (la real, no la ofuscada).

Recomendación: 1Password / Bitwarden / Vaultwarden / KeepassXC, con copia
off-site del propio gestor (no en el mismo VPS).

### Restauración

```bash
cd /opt/crmbo

# Modo interactivo: lista los snapshots y pregunta cuál usar.
sudo bash scripts/restore-mysql-restic.sh

# Modo directo: último snapshot disponible.
sudo bash scripts/restore-mysql-restic.sh latest

# Modo directo: snapshot concreto (prefijo de 8 chars del id).
sudo bash scripts/restore-mysql-restic.sh 1a2b3c4d

# Simulación sin tocar nada (recomendado antes del primer restore real).
sudo bash scripts/restore-mysql-restic.sh latest --dry-run
```

Flujo:

1. Pide confirmación explícita `Type RESTORE to confirm`.
2. `restic restore` a `/tmp/restore-<timestamp>/`.
3. `docker compose stop api frontend` (evita escrituras durante la
   importación).
4. `gunzip -c <dump> | docker compose exec -T db mysql ...`.
5. `docker compose start api frontend`.
6. Limpia el directorio temporal con `trap EXIT`.

### Política de retención

`restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --tag daily`

≈ 23 snapshots: la última semana en granularidad diaria, el último mes
en semanal y el último año en mensual. Espacio aproximado en HiDrive:
1-3 GB (deduplicación + compresión del mysqldump gzipped).

### Verificación trimestral (recomendado)

Cada 3 meses, lanzar manualmente la verificación profunda — descarga el
5% de los blobs y los descifra para detectar bit rot que el `restic
check` mensual no atrapa:

```bash
sudo bash -c '. /etc/crmbo/backup.env && restic check --read-data-subset 5%'
```

### Monitorización de fallos

```bash
# Tail del log estructurado del backup
sudo tail -f /var/log/crmbo-backup.log

# Salida de cron en el journal
sudo journalctl -t cron --since '24h ago'

# Última ejecución
sudo grep '=== Backup' /var/log/crmbo-backup.log | tail
```

Si configuras `BACKUP_FAILURE_WEBHOOK` en `/etc/crmbo/backup.env`, los
fallos disparan adicionalmente un `POST` con JSON:

```json
{
  "host": "vps01.example.com",
  "error": "Backup failed with exit code 2 (line 87)",
  "timestamp": "2026-05-10T03:00:14Z"
}
```

Útil para enchufarlo a Slack/Discord/Healthchecks.io.

### Verificación manual end-to-end

`scripts/test-backup-hidrive.sh` ejecuta el ciclo completo bajo demanda:

```bash
sudo bash /opt/crmbo/scripts/test-backup-hidrive.sh
```

1. Lanza el backup script directamente.
2. Lista todos los snapshots tras la ejecución.
3. Imprime `restic stats` (tamaño lógico + tamaño de restore).
4. Sugiere el comando de dry-run restore como cierre.

### Checklist al día siguiente del primer setup

- [ ] `sudo cat /var/log/crmbo-backup.log` muestra una línea
      `=== Backup OK: snapshot=... ===` con timestamp ~03:00 UTC.
- [ ] `sudo bash -c '. /etc/crmbo/backup.env && restic snapshots'` lista
      el snapshot recién creado.
- [ ] `sudo bash /opt/crmbo/scripts/restore-mysql-restic.sh latest --dry-run`
      termina sin errores y describe el plan.
- [ ] `RESTIC_PASSWORD` y `HIDRIVE_*` están almacenadas en gestor de
      contraseñas con copia off-site.

### Fuera de alcance (Phase B futuro)

* Sin replicación a un segundo backend (p. ej. AWS S3 / Backblaze B2)
  además de HiDrive. Si HiDrive y el VPS caen a la vez, sigue habiendo
  un único proveedor.
* Sin healthcheck push (tipo Healthchecks.io); de momento solo webhook
  push en fallo.
* Sin backup del volumen `redis_data` (Redis se usa como caché, sin
  estado crítico hoy).
* Sin backup automático del `.env.production` o de `INTEGRATION_SECRETS_KEY`:
  esos deben vivir en el gestor de contraseñas, no en el repositorio
  restic.
