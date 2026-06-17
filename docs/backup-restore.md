# Sprint Backup — descifrar y restaurar

Procedimiento que Bart sigue cuando necesita **recuperar** un backup
(corrupción de DB, redeploy en VPS nuevo, sospecha de intrusión, o
simple curiosidad sobre datos antiguos).

Cada archivo de backup es un `.tar.gz.gpg` que contiene:

```
db.sql           # mysqldump --all-databases --routines --triggers
env.production   # copia exacta de /opt/crmbo/.env.production
```

> Ambos son altamente sensibles. NO los muevas a un disco no cifrado;
> NO los abras en un equipo compartido. El `env.production` lleva las
> claves de Gmail, Brevo, AgileCRM, Sentry y la passphrase de los
> propios backups.

---

## 1. Obtener el archivo

Tres caminos:

### A. Desde la UI admin

1. Login como admin → `/admin/backups`.
2. Pulsa el botón de descarga (icono Download) en la fila deseada.
3. El navegador descarga `backup_YYYYMMDD_HHMMSS.tar.gz.gpg`.

### B. Desde el VPS (si tienes SSH)

```bash
scp root@<vps>:/var/backups/crmbo/backup_YYYYMMDD_HHMMSS.tar.gz.gpg ./
```

### C. Desde Google Drive

Abre `drive.google.com` → `CRMBO_Backups/` → click derecho → Descargar.

Esta vía sirve cuando el VPS está caído (escenario "recuperación
total"): los backups de los últimos meses viven en Drive incluso si
la rotación FIFO del disco ya los borró.

---

## 2. Descifrar el archivo

Necesitas la **passphrase** guardada en el password manager (ver
[`backup-setup.md`](./backup-setup.md) paso 4).

```bash
gpg --decrypt --batch \
    --passphrase "TU_PASSPHRASE" \
    backup_YYYYMMDD_HHMMSS.tar.gz.gpg \
    > backup.tar.gz
```

Si la passphrase es interactiva (preferible — no quedan rastros en el
historial de shell):

```bash
gpg --decrypt backup_YYYYMMDD_HHMMSS.tar.gz.gpg > backup.tar.gz
# (te pide la passphrase por prompt)
```

Verifica:

```bash
tar -tzf backup.tar.gz
# db.sql
# env.production
```

---

## 3. Extraer

```bash
mkdir restore && cd restore
tar -xzf ../backup.tar.gz
ls -l
# -rw------- ... db.sql
# -rw------- ... env.production
```

Asegúrate de NO sobrescribir tu `.env.production` actual sin
backuparlo primero (`cp .env.production .env.production.before-restore`).

---

## 4. Restaurar MySQL

### 4a. En VPS existente (rollback)

Antes de restaurar, **para la API** para que no haya escrituras
concurrentes:

```bash
cd /opt/crmbo
docker compose -f docker-compose.prod.yml stop api worker
```

Conserva el dump actual (para volver atrás si el rollback sale mal):

```bash
docker compose -f docker-compose.prod.yml exec -T db \
  mysqldump --all-databases --routines --triggers --single-transaction \
  -u root -p"$MYSQL_ROOT_PASSWORD" \
  > /tmp/pre-restore-$(date -u +%Y%m%dT%H%M%SZ).sql
```

Restaura:

```bash
docker compose -f docker-compose.prod.yml exec -T db \
  mysql -u root -p"$MYSQL_ROOT_PASSWORD" \
  < restore/db.sql
```

Levanta otra vez:

```bash
docker compose -f docker-compose.prod.yml up -d api worker
```

### 4b. En VPS limpio (recuperación total)

Asume que ya has clonado el repo en `/opt/crmbo` y configurado Docker
(ver `docs/deployment-ionos.md`).

1. Copia el `env.production` restaurado a `/opt/crmbo/.env.production`
   (después de revisarlo — puede contener API keys rotadas durante el
   incidente):

   ```bash
   cp restore/env.production /opt/crmbo/.env.production
   chmod 600 /opt/crmbo/.env.production
   ```

2. Arranca solo el MySQL primero:

   ```bash
   cd /opt/crmbo
   docker compose -f docker-compose.prod.yml up -d db
   ```

3. Espera a que el contenedor termine de inicializar (~30 s) y
   restaura:

   ```bash
   docker compose -f docker-compose.prod.yml exec -T db \
     mysql -u root -p"$MYSQL_ROOT_PASSWORD" \
     < restore/db.sql
   ```

4. Levanta el resto:

   ```bash
   docker compose -f docker-compose.prod.yml up -d
   ```

5. Aplica migraciones nuevas (si el código en `main` es más reciente
   que el dump):

   ```bash
   docker compose -f docker-compose.prod.yml exec api \
     alembic upgrade head
   ```

---

## 5. Verificación post-restore

| Test | Comando | Esperado |
|------|---------|----------|
| API arriba | `curl https://crm.bomedia.net/api/healthz` | 200 |
| Login | UI `/login` con tu cuenta | OK |
| Conteo contactos | `SELECT COUNT(*) FROM contacts` | igual al dump |
| Audit reciente | `/admin/audit` última fila | refleja el restore |

> Si la fila del audit refleja el restore (ej. evento
> `auth.login_success` post-restauración), todo OK. Si NO hay
> eventos posteriores a la fecha del backup, la app está leyendo de
> una DB vieja — revisa el contenedor `db` y los volúmenes.

---

## 6. Re-encriptar el backup tras manipularlo

Si descifraste un backup para inspeccionarlo y NO quieres dejar la
copia en claro en disco:

```bash
shred -u backup.tar.gz
shred -u -r restore/
```

`shred -u` sobrescribe el contenido antes de borrar — más seguro que
`rm` en discos magnéticos. En SSDs el wear-leveling reduce su
eficacia; aún así no deja la versión legible en el sistema de
ficheros.

---

## Notas de seguridad

- **Rotación de passphrase**: si crees que la passphrase se ha
  comprometido, genera una nueva (ver `backup-setup.md`), actualiza
  `.env.production`, dispara un backup manual nuevo. Los backups
  anteriores siguen siendo descifrables con la passphrase vieja —
  bórralos del disco + de Drive si quieres invalidarlos.
- **Acceso a `/admin/backups`**: el endpoint `download` solo está
  abierto a `role=admin`. Manager+ NO pueden descargar (la passphrase
  no neutraliza la sensibilidad del fichero).
- **Auditoría**: los eventos `backup.triggered` y `backup.deleted`
  quedan en `/admin/audit` con el `actor` que los disparó.
- **Drive como copia off-site**: el archivo en Drive vive en la
  cuenta Google del owner; ni IONOS ni un atacante con root en el
  VPS pueden borrarlo desde allí. La copia local (`/var/backups/`)
  sí — por eso Drive es la pieza importante.

---

## Ver también

- [`backup-setup.md`](./backup-setup.md) — instalación manual en
  VPS (rclone, cron, passphrase).
- [`backups-and-restore.md`](./backups-and-restore.md) — estrategia
  pre-Sprint-Backup (Plesk + restic). Sigue vigente como tercera
  capa.
- [`deployment-ionos.md`](./deployment-ionos.md) — bootstrap del VPS.
