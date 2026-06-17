# Sprint Backup — pasos manuales en el VPS

Este documento describe los pasos que **Bart** debe ejecutar UNA vez
en el VPS tras mergear el PR de Sprint Backup. Cubren la parte que
Claude Code no puede hacer (requieren navegador, credenciales Google,
o acceso SSH como root).

El código de la app (router admin, modelos, migración, bash script,
UI `/admin/backups`) ya está en `main`. Sin estos 5 pasos manuales,
el sistema arranca pero los backups NO se generan / no se suben a
Drive / la passphrase no existe y el script aborta.

---

## 1. Instalar rclone

En el VPS, como root:

```bash
ssh root@<crmbo-vps>
dnf install rclone -y
# Verifica:
rclone version
```

Si la distro no es RHEL / Rocky / Alma, usa:

```bash
curl https://rclone.org/install.sh | sudo bash
```

---

## 2. Configurar rclone contra Google Drive

Esto requiere autorizar la app en el navegador. NO es tractable desde
CI / agente — Bart lo hace en local.

```bash
rclone config
```

El wizard interactivo:

```
n) New remote
name> drive
Storage> drive
client_id> [Enter para usar el client_id por defecto]
client_secret> [Enter]
scope> 1   (Full access to all files)
service_account_file> [Enter]
Edit advanced config> n
Use auto config> n   (estamos por SSH, sin navegador local en VPS)
```

rclone imprime una URL larga. Bart la abre en su navegador local con
la cuenta Google donde quiere los backups (`info@bomedia.net` o una
dedicada). Después de aceptar, Google devuelve un código que Bart
pega en la prompt de rclone.

```
Configure as Shared Drive> n
y) Yes this is OK
q) Quit config
```

Verifica:

```bash
rclone listremotes
# debería imprimir: drive:
rclone lsd drive:
# lista las carpetas raíz del Drive
```

---

## 3. Crear la carpeta `CRMBO_Backups` en Drive

Dos opciones equivalentes:

**Opción A** — vía rclone (sin navegador):

```bash
rclone mkdir drive:CRMBO_Backups
```

**Opción B** — desde la UI de Google Drive del navegador:

1. Abre [drive.google.com](https://drive.google.com).
2. Carpeta nueva, nombre exacto: `CRMBO_Backups`.

> El nombre se referencia desde la variable de entorno
> `RCLONE_REMOTE` del script. Si Bart quiere otro nombre, ajusta
> también esa variable en `.env.production`.

---

## 4. Generar passphrase de encriptación

**Una sola vez** — la passphrase cifra cada backup. Si se pierde,
los archivos `.tar.gz.gpg` son irrecuperables.

```bash
# Genera 32 caracteres random (base64). Copia el output.
openssl rand -base64 32
```

Añádela a `/opt/crmbo/.env.production` como una línea nueva:

```bash
BACKUP_ENCRYPTION_PASSPHRASE=<el-string-de-32-chars>
```

Inmediatamente después, **guarda una copia en el password manager**
(1Password, Bitwarden, etc.). Sin esa copia, los backups son
inútiles.

Verifica permisos del archivo (no debería ser legible por usuarios
distintos del owner):

```bash
chmod 600 /opt/crmbo/.env.production
ls -l /opt/crmbo/.env.production
# -rw------- 1 root root ...
```

---

## 5. Instalar el cron + script

Copia el script al VPS (asumiendo que el repo está clonado en
`/opt/crmbo`):

```bash
sudo cp /opt/crmbo/scripts/backup-crmbo.sh /opt/crmbo/scripts/
sudo chmod +x /opt/crmbo/scripts/backup-crmbo.sh
sudo mkdir -p /var/backups/crmbo
sudo chmod 700 /var/backups/crmbo
sudo touch /var/log/crmbo-backup.log
sudo chmod 640 /var/log/crmbo-backup.log
```

Edita el crontab del usuario root (el cron necesita acceso a docker
y a `/opt/crmbo/.env.production`):

```bash
sudo crontab -e
```

Añade la línea:

```cron
# Sprint Backup. Cada 72 h a las 03:00 UTC. La CLI de Python
# inserta la row en la tabla `backups` y luego ejecuta el bash.
0 3 */3 * * cd /opt/crmbo/backend && /opt/crmbo/.venv/bin/python -m app.backups.cli >> /var/log/crmbo-backup.log 2>&1
```

> Si la app NO está en `/opt/crmbo` o el venv vive en otro path,
> ajusta las rutas. Si NO usas venv y la instalación de Python es
> global, sustituye `/opt/crmbo/.venv/bin/python` por `python3`.
>
> Alternativa: si Bart prefiere que el script bash maneje la BD
> directamente (sin pasar por la CLI Python), puede invocar el bash
> a pelo (`0 3 */3 * * /opt/crmbo/scripts/backup-crmbo.sh >> ...`)
> — pero entonces NO habrá row en la tabla `backups` para los
> ejecutados por cron, solo para los manuales. El doc recomienda la
> CLI Python para que el histórico esté completo.

---

## 6. Verificación end-to-end

1. **Disparo manual desde UI**:
   - Inicia sesión como admin → `/admin/backups`.
   - Pulsa "📦 Crear backup ahora".
   - Espera 5-10 min (mysqldump grande puede tardar).
   - La fila pasa de "En curso" a "OK" con tamaño y enlace Drive.

2. **Inspecciona disco**:

   ```bash
   ls -lh /var/backups/crmbo/
   # backup_20260618_030001.tar.gz.gpg   15M
   ```

3. **Inspecciona Drive**:
   - Abre Google Drive → `CRMBO_Backups/` → debería aparecer el
     archivo recién subido.

4. **Prueba el descifrado en local**:

   - Descarga el archivo desde `/admin/backups` (botón Download).
   - En local:

     ```bash
     gpg --decrypt --batch --passphrase "TU_PASSPHRASE" \
       backup_20260618_030001.tar.gz.gpg \
       > backup.tar.gz
     tar -tzf backup.tar.gz
     # db.sql
     # env.production
     ```

5. **Espera al siguiente cron** (3 días después) → la fila aparece
   con `triggered_by='cron'`.

6. **Rotación**: tras el 4º backup, el más antiguo del disco
   desaparece (la fila en BD permanece — solo el binario se borra).

---

## Variables de entorno relevantes

Definidas en `/opt/crmbo/.env.production`:

| Variable                          | Default                  | Descripción |
|-----------------------------------|--------------------------|-------------|
| `BACKUP_ENCRYPTION_PASSPHRASE`    | (required)               | Passphrase GPG simétrica. ≥ 16 chars. |
| `BACKUP_DIR`                      | `/var/backups/crmbo`     | Carpeta destino del `.tar.gz.gpg`. |
| `BACKUP_RETAIN`                   | `3`                      | Cuántos archivos retener (FIFO). |
| `RCLONE_REMOTE`                   | `drive:CRMBO_Backups`    | Remote rclone + carpeta. Vacío = skip Drive. |
| `BACKUP_SCRIPT_PATH`              | `/opt/crmbo/scripts/backup-crmbo.sh` | Override solo si Bart instala en otra ruta. |
| `MYSQL_ROOT_PASSWORD`             | (ya existía)             | Lo lee mysqldump dentro del bash. |

---

## Troubleshooting

### El cron no ejecuta
- `sudo journalctl -u cron --since "1 hour ago" | grep crmbo` para ver
  si el cron fue invocado.
- `tail -100 /var/log/crmbo-backup.log` para ver el output del último
  run.

### "mysqldump muerto"
- El contenedor `db` está down: `docker compose -f docker-compose.prod.yml ps`.
- Falta `MYSQL_ROOT_PASSWORD` en `.env.production`.

### "rclone copy falló"
- El refresh token caducó: re-ejecuta `rclone config reconnect drive:`.
- Carpeta `CRMBO_Backups` borrada manualmente en Drive: créala otra
  vez (paso 3).

### Backup queda en "running" para siempre
- El worker RQ murió en mitad. La UI marca como FAILED tras 1 h
  (lo hace el propio `POST /create` la siguiente vez que alguien
  dispare un backup).
- Forzar inmediato: borra la fila desde la UI con el botón "Borrar"
  (o `DELETE /api/admin/backups/{id}`).

---

Para el procedimiento de descifrar + restaurar en VPS limpio, ver
[`backup-restore.md`](./backup-restore.md).
