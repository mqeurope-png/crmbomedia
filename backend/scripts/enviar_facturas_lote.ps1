<#
.SYNOPSIS
  Envía facturas por email EN LOTE conduciendo el envío que BoHub ya tiene (F-1,
  PR #359): correo NUEVO al cliente, con el PDF de la factura y el idioma que
  resuelve la cascada de la app. NO reimplementa nada de correo ni de PDF.

.DESCRIPTION
  Para cada pedido del CSV: resuelve su factura de FACTUSOL (serie+código),
  comprueba que está facturado y que NO se ha enviado ya (timeline F-1), pide la
  previsualización de F-1 (destinatario, asunto, idioma, cuerpo) y —con -Apply—
  envía como correo NUEVO (reply_to_message_id = null; no responde a hilos).

  Dry-run por defecto: enseña qué haría (cliente, email, idioma, REMITENTE, asunto,
  y si ya se envió) sin enviar nada. El remitente lo decide BoHub por la serie de
  la factura (empresa emisora), no el script. Con -Apply pide confirmación («escribe SI»),
  muestra los destinatarios y envía de uno en uno, deteniéndose ante el primer
  error. Son clientes reales y el envío es irreversible → revisa el dry-run.

.EXAMPLE
  .\enviar_facturas_lote.ps1 -BaseUrl https://tu-dominio -Csv .\pedidos_enviar.csv
.EXAMPLE
  .\enviar_facturas_lote.ps1 -BaseUrl https://tu-dominio -Csv .\pedidos_enviar.csv -Apply
#>

[CmdletBinding()]
param(
    [string]$BaseUrl = "",
    # CSV con una columna: numero_pedido (número web desnudo, p.ej. 99928).
    [string]$Csv = ".\pedidos_enviar.csv",
    [switch]$Apply,
    [int]$PausaSegundos = 3,
    [string]$LogFile = ".\enviar_facturas_$(Get-Date -Format yyyyMMdd_HHmmss).log"
)

$ErrorActionPreference = "Stop"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Get-ErrorBody($err) {
    try {
        if ($err.ErrorDetails -and $err.ErrorDetails.Message) { return $err.ErrorDetails.Message }
        $resp = $err.Exception.Response
        if ($resp) {
            $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
            return $reader.ReadToEnd()
        }
    } catch {}
    return $err.Exception.Message
}
function Get-HttpStatus($err) {
    try { return [int]$err.Exception.Response.StatusCode } catch { return 0 }
}

Start-Transcript -Path $LogFile -Append | Out-Null
Write-Host "== Envío de facturas por email en lote (conduce el envío F-1 de BoHub) ==" -ForegroundColor Cyan

if (-not $BaseUrl) { $BaseUrl = Read-Host "URL base de BoHub (p.ej. https://tu-dominio)" }
$BaseUrl = $BaseUrl.TrimEnd("/")
if (-not $BaseUrl.StartsWith("http")) { Stop-Transcript | Out-Null; throw "BaseUrl no válida: $BaseUrl" }
if (-not (Test-Path $Csv)) { Stop-Transcript | Out-Null; throw "No encuentro el CSV: $Csv (columna: numero_pedido)" }

# --- 1) Login (mismo esquema que emitir_facturas_lote.ps1) ------------------
$email = Read-Host "Email BoHub"
$secPass = Read-Host "Contraseña" -AsSecureString
$plain = [System.Net.NetworkCredential]::new("", $secPass).Password
Write-Host "Autenticando en $BaseUrl ..." -ForegroundColor Gray
try {
    $login = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/auth/login" `
        -ContentType "application/json" -Body (@{ email = $email; password = $plain } | ConvertTo-Json)
} catch {
    Write-Host "ERROR de login: $(Get-ErrorBody $_)" -ForegroundColor Red
    Stop-Transcript | Out-Null; exit 1
} finally { $plain = $null }
if ($login.requires_2fa) {
    Write-Host "Tu usuario tiene 2FA; este script no completa el 2FA. Usa un usuario sin 2FA (rol admin/pedidos)." -ForegroundColor Red
    Stop-Transcript | Out-Null; exit 1
}
$headers = @{ Authorization = "Bearer $($login.access_token)" }
Write-Host "Login OK (token ~8 h)." -ForegroundColor Green

# --- 2) CSV -----------------------------------------------------------------
$filas = Import-Csv -Path $Csv
if (-not $filas) { Stop-Transcript | Out-Null; throw "El CSV está vacío." }
foreach ($f in $filas) { if (-not $f.numero_pedido) { throw "El CSV debe tener la columna 'numero_pedido'." } }
Write-Host "CSV: $($filas.Count) pedidos." -ForegroundColor Gray

# --- 3) Mapa nº pedido → pedido de BoHub ------------------------------------
$lista = Invoke-RestMethod -Uri "$BaseUrl/api/erp/orders?limit=500" -Headers $headers
$porNumero = @{}
foreach ($o in $lista.items) {
    $bare = ($o.order_number -split "-")[-1]
    if (-not $porNumero.ContainsKey($bare)) { $porNumero[$bare] = $o }
}

function Test-YaEnviada($orderId) {
    try {
        $tl = Invoke-RestMethod -Headers $headers `
            -Uri "$BaseUrl/api/erp/orders/$orderId/timeline?types=audit&limit=100"
        foreach ($ev in $tl.items) { if ($ev.title -eq "erp.invoice_emailed") { return $true } }
    } catch {}
    return $false
}

# --- 4) Plan ----------------------------------------------------------------
$plan = @()
foreach ($f in $filas) {
    $num = "$($f.numero_pedido)".Trim()
    $o = $porNumero[$num]
    $row = [pscustomobject]@{
        numero = $num; id = $null; cliente = ""; email = ""; idioma = ""
        asunto = ""; serie = $null; codigo = $null; estado = ""; enviable = $false
        subject = ""; body = ""; from_alias = ""; remitente = ""
    }
    if (-not $o) { $row.estado = "NO ENCONTRADO (revisa el número o sube el límite)"; $plan += $row; continue }
    $row.id = $o.id
    $row.cliente = @($o.company_name, $o.contact_name | Where-Object { $_ }) -join " · "
    # ¿Tiene factura en FACTUSOL?
    try {
        $ref = Invoke-RestMethod -Headers $headers -Uri "$BaseUrl/api/erp/orders/$($o.id)/factusol-invoice-ref"
        $row.serie = $ref.serie; $row.codigo = $ref.codigo
    } catch {
        if ((Get-HttpStatus $_) -eq 404) { $row.estado = "SIN FACTURA en FACTUSOL (se salta)"; $plan += $row; continue }
        $row.estado = "error al resolver factura: $(Get-ErrorBody $_)"; $plan += $row; continue
    }
    if (Test-YaEnviada $o.id) { $row.estado = "YA ENVIADA (se salta)"; $plan += $row; continue }
    # Previsualización F-1 (no envía nada): destinatario, asunto, idioma, cuerpo.
    try {
        $pv = Invoke-RestMethod -Headers $headers `
            -Uri "$BaseUrl/api/erp/factusol/documents/facturas/$($ref.serie)/$($ref.codigo)/email-preview"
    } catch {
        $row.estado = "error en previsualización: $(Get-ErrorBody $_)"; $plan += $row; continue
    }
    $row.email = $pv.to; $row.idioma = $pv.lang; $row.asunto = $pv.subject
    $row.subject = $pv.subject; $row.body = $pv.body_text; $row.from_alias = $pv.from_alias
    # Remitente que decidirá BoHub: el alias de la empresa emisora de la serie
    # (from_alias_source="serie") o, si esa serie no lo tiene, el del usuario.
    $row.remitente = $pv.from_alias
    if ($pv.from_alias_source -eq "serie") { $row.remitente = "$($pv.from_alias) (serie)" }
    if (-not $pv.to) { $row.estado = "SIN EMAIL del cliente (se salta)" }
    else { $row.estado = "listo"; $row.enviable = $true }
    $plan += $row
}

Write-Host ""
Write-Host "== PLAN ==" -ForegroundColor Cyan
$plan | Format-Table numero, cliente, email, idioma, remitente, asunto, estado -AutoSize | Out-String | Write-Host
$aEnviar = @($plan | Where-Object { $_.enviable })
Write-Host ("A enviar: {0} · Saltados/sin factura/ya enviadas/sin email: {1}" -f `
    $aEnviar.Count, ($plan.Count - $aEnviar.Count)) -ForegroundColor Yellow

# --- 5) Dry-run por defecto -------------------------------------------------
if (-not $Apply) {
    Write-Host "`nMODO PREVISUALIZACIÓN (dry-run): no se ha enviado nada." -ForegroundColor Green
    Write-Host "Para enviar de verdad, vuelve a ejecutar con  -Apply" -ForegroundColor Green
    Stop-Transcript | Out-Null; exit 0
}
if ($aEnviar.Count -eq 0) { Write-Host "No hay nada que enviar." -ForegroundColor Green; Stop-Transcript | Out-Null; exit 0 }

# --- 6) Confirmación --------------------------------------------------------
Write-Host "`nSe enviará a estos destinatarios:" -ForegroundColor Yellow
$aEnviar | ForEach-Object { Write-Host ("  {0} → {1} ({2}) · desde {3}" -f $_.numero, $_.email, $_.idioma, $_.from_alias) }
$resp = Read-Host ("`nVas a ENVIAR {0} facturas por email (correo nuevo al cliente). Escribe SI para continuar" -f $aEnviar.Count)
if ($resp -ne "SI") { Write-Host "Cancelado. No se ha enviado nada." -ForegroundColor Yellow; Stop-Transcript | Out-Null; exit 0 }

# --- 7) Envío de una en una, deteniéndose ante el primer error --------------
$enviadas = @(); $fallo = $null
foreach ($p in $aEnviar) {
    Write-Host ""
    Write-Host ("→ {0} → {1} ({2})" -f $p.numero, $p.email, $p.idioma) -ForegroundColor Cyan
    try {
        $body = @{
            confirm = $true
            to = @($p.email)
            subject = $p.subject
            body_text = $p.body
            lang = $p.idioma
            from_alias = $p.from_alias
            reply_to_message_id = $null   # correo NUEVO: no responder a hilos
        } | ConvertTo-Json
        $res = Invoke-RestMethod -Method Post -ContentType "application/json" -Headers $headers `
            -Uri "$BaseUrl/api/erp/factusol/documents/facturas/$($p.serie)/$($p.codigo)/email" -Body $body
    } catch {
        $code = Get-HttpStatus $_
        $fallo = "Pedido $($p.numero): error al enviar (HTTP $code): $(Get-ErrorBody $_)"
        break
    }
    Write-Host ("  OK → enviada (factura {0}, msg {1})" -f $res.numero, $res.message_id) -ForegroundColor Green
    $enviadas += [pscustomobject]@{ numero = $p.numero; email = $p.email; factura = $res.numero }
    Start-Sleep -Seconds $PausaSegundos
}

# --- 8) Resumen -------------------------------------------------------------
Write-Host ""
Write-Host "== RESUMEN ==" -ForegroundColor Cyan
Write-Host ("Enviadas: {0}" -f $enviadas.Count) -ForegroundColor Green
$enviadas | Format-Table numero, email, factura -AutoSize | Out-String | Write-Host
$saltadas = @($plan | Where-Object { -not $_.enviable })
Write-Host ("Saltadas (sin factura / ya enviadas / sin email / no encontradas): {0}" -f $saltadas.Count) -ForegroundColor Yellow
if ($fallo) {
    Write-Host "DETENIDO ante un error (no se siguieron enviando el resto):" -ForegroundColor Red
    Write-Host "  $fallo" -ForegroundColor Red
    Write-Host "Corrige y vuelve a ejecutar: las ya enviadas se saltan solas. Log: $LogFile" -ForegroundColor Red
    Stop-Transcript | Out-Null; exit 1
}
Write-Host "Lote enviado sin errores. Log: $LogFile" -ForegroundColor Green
Stop-Transcript | Out-Null; exit 0
