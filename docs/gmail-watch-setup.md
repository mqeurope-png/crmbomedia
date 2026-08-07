# Gmail Watch + Pub/Sub — setup de Google Cloud (CRM-GMAIL)

Real-time del correo entrante: Gmail publica un aviso en un topic de Cloud
Pub/Sub cada vez que cambia el buzón; una suscripción **push** lo reenvía a
`POST https://bo-crm.mbolasers.com/api/webhooks/gmail`, que encola el trabajo
y responde en <1 s. El backend procesa el `historyId` en un worker.

> El endpoint ya vive bajo `/api/webhooks/gmail` (el proxy nginx ya enruta
> `/api/*`; **no** hace falta regla nueva). No es `/webhooks/gmail`.

Este setup lo hace **Bart una vez** (5-10 min). Sin él, el push no valida y
sólo funcionará el poller de respaldo (cada 15 min, no es tiempo real).

## 1. Consola de Google Cloud

En el proyecto que aloja las credenciales OAuth actuales (el mismo
`GMAIL_PUBSUB_PROJECT_ID`):

1. **Habilitar Pub/Sub API** (APIs & Services → Enable APIs → "Cloud Pub/Sub API").
2. **Crear el topic** `crmbo-gmail-notifications`.
3. **Dar permiso de publicación a Gmail** — en el topic → *Permissions* →
   *Add principal*: `gmail-api-push@system.gserviceaccount.com` con el rol
   **`Pub/Sub Publisher`**. (Gmail publica los avisos como ese SA.)
4. **Crear la suscripción push** sobre ese topic:
   - Delivery type: **Push**.
   - Endpoint URL: `https://bo-crm.mbolasers.com/api/webhooks/gmail`.
   - **Enable authentication** → elige/crea un **service account** (p.ej.
     `crmbo-pubsub-push@<project>.iam.gserviceaccount.com`). Pub/Sub firmará
     cada push con un OIDC token de ese SA, con `aud` = la URL del endpoint.

## 2. Comandos `gcloud` equivalentes (alternativa a la consola)

```bash
PROJECT_ID=<tu-project-id>
gcloud config set project "$PROJECT_ID"

# 1) API
gcloud services enable pubsub.googleapis.com

# 2) Topic
gcloud pubsub topics create crmbo-gmail-notifications

# 3) Permiso de publicación para Gmail
gcloud pubsub topics add-iam-policy-binding crmbo-gmail-notifications \
  --member="serviceAccount:gmail-api-push@system.gserviceaccount.com" \
  --role="roles/pubsub.publisher"

# 4) Service account para el push + suscripción push autenticada
gcloud iam service-accounts create crmbo-pubsub-push \
  --display-name="CRMBo Pub/Sub push"

gcloud pubsub subscriptions create crmbo-gmail-push \
  --topic=crmbo-gmail-notifications \
  --push-endpoint="https://bo-crm.mbolasers.com/api/webhooks/gmail" \
  --push-auth-service-account="crmbo-pubsub-push@${PROJECT_ID}.iam.gserviceaccount.com"
```

## 3. Variables de entorno (`.env.production`)

```dotenv
GMAIL_PUBSUB_PROJECT_ID=<tu-project-id>
GMAIL_PUBSUB_TOPIC=projects/<tu-project-id>/topics/crmbo-gmail-notifications
GMAIL_PUBSUB_SUBSCRIPTION=projects/<tu-project-id>/subscriptions/crmbo-gmail-push
# Verificación fuerte del JWT del push (recomendado):
GMAIL_WEBHOOK_JWT_AUDIENCE=https://bo-crm.mbolasers.com/api/webhooks/gmail
GMAIL_WEBHOOK_SERVICE_ACCOUNT_EMAIL=crmbo-pubsub-push@<tu-project-id>.iam.gserviceaccount.com
```

Con `GMAIL_WEBHOOK_JWT_AUDIENCE` + `GMAIL_WEBHOOK_SERVICE_ACCOUNT_EMAIL`
puestos, el webhook exige: firma válida de Google (emisor `accounts.google.com`),
`aud` == la audiencia configurada y `email` == ese service account. Si NO se
configura ninguna verificación (ni estos dos ni el viejo
`GMAIL_PUBSUB_VERIFICATION_TOKEN`), el webhook **acepta** los push sin firma y
lo registra como warning — pon las envs para cerrarlo.

## 4. Deploy y arranque del Watch

Tras desplegar (ver el bloque de deploy del PR), registra el watch **una vez**:

```bash
docker exec crmbo-api python -m app.integrations.gmail_watch register_watch
```

La renovación es automática: al arrancar, la API arma un cron **diario** que
re-registra el Watch cuando le quedan <24 h (el Watch de Gmail expira a los 7
días, así que se renueva ~cada 6). También se arma un **poller de respaldo cada
15 min** que recupera correo si el push fallara (emite warning si recupera >0).

Comandos manuales del módulo (por si hicieran falta):

```bash
python -m app.integrations.gmail_watch register_watch          # registrar/renovar ya
python -m app.integrations.gmail_watch renew_watch_if_expiring # renovar solo si caduca <24h
python -m app.integrations.gmail_watch unregister_watch        # parar el watch (cleanup)
```

## 5. Comprobación rápida

- Envía un email externo a un alias configurado y activo. En <10 s aparece en
  la bandeja del dueño del alias (aunque el remitente no sea un contacto).
- Los logs del `api` muestran `gmail.webhook.enqueued`; los del `worker-sync`,
  el `process_history`. Si ves `gmail.poll_fallback recovered=N>0`, el push no
  está entrando: revisa la suscripción / la audiencia del JWT.
