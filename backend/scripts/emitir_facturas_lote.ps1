<#
.SYNOPSIS
  Emite facturas en LOTE conduciendo la emisión que BoHub YA tiene (el botón
  azul «Solicitar/Emitir factura»). NO escribe en FACTUSOL directamente: solo
  llama al endpoint `emit-factusol-invoice`, así hereda todo el motor probado
  (mapper, allowlist, next_codfac correlativo, marcado ESTPCL) y deja el pedido
  sincronizado en BoHub.

.DESCRIPTION
  Flujo:
    1. Login (email + contraseña segura) → token JWT (Bearer, 8 h).
    2. Lee un CSV `numero_pedido,serie` (lo rellena Bart).
    3. Resuelve el id interno (UUID) de cada pedido por su número de pedido web.
    4. Salta los que YA están facturados.
    5. Por defecto DRY-RUN: enseña qué haría sin emitir nada.
    6. Con -Apply: pide confirmación («escribe SI») y emite de uno en uno,
       esperando el resultado (nº de factura) de cada uno.
    7. Se DETIENE ante el primer error real (no sigue en cascada).
    8. Resumen final + log en fichero.

  Seguridad/alcance: usa SOLO el endpoint de emisión existente. No fuerza
  números de factura (la numeración correlativa la pone BoHub). No toca
  pedidos ya facturados. No escribe ninguna tabla de FACTUSOL.

.EXAMPLE
  # Previsualización (no emite nada):
  .\emitir_facturas_lote.ps1 -BaseUrl https://tu-dominio -Csv .\pedidos.csv

.EXAMPLE
  # Emitir de verdad (pide confirmación por teclado):
  .\emitir_facturas_lote.ps1 -BaseUrl https://tu-dominio -Csv .\pedidos.csv -Apply
#>

[CmdletBinding()]
param(
    # URL base de BoHub (sin barra final). Ej: https://crm.tudominio.com
    [string]$BaseUrl = "",
    # CSV con columnas: numero_pedido,serie   (una fila por pedido)
    [string]$Csv = ".\pedidos.csv",
    # Sin este switch, el script NO emite (dry-run). Con él, emite tras confirmar.
    [switch]$Apply,
    # Segundos de pausa entre emisiones (no saturar el worker de FACTUSOL).
    [int]$PausaSegundos = 3,
    # Fichero de log (por defecto, con marca de tiempo).
    [string]$LogFile = ".\emitir_facturas_$(Get-Date -Format yyyyMMdd_HHmmss).log"
)

$ErrorActionPreference = "Stop"
# TLS 1.2 (Windows PowerShell 5.1 a veces negocia TLS 1.0 por defecto).
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

# Estados de facturación que cuentan como YA FACTURADO (no re-emitir).
$YA_FACTURADO = @("generated", "invoiced_by_erp", "already_invoiced_externally")

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

function Get-ErrorBody($err) {
    # Extrae el cuerpo del error HTTP (el detalle de FastAPI, p.ej.
    # BDExisteRegistro) tanto en PowerShell 7 como en 5.1.
    try {
        if ($err.ErrorDetails -and $err.ErrorDetails.Message) {
            return $err.ErrorDetails.Message           # PowerShell 7+
        }
        $resp = $err.Exception.Response
        if ($resp) {
            $stream = $resp.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($stream)
            return $reader.ReadToEnd()                 # PowerShell 5.1
        }
    } catch {}
    return $err.Exception.Message
}

function Get-HttpStatus($err) {
    try { return [int]$err.Exception.Response.StatusCode } catch { return 0 }
}

# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------

Start-Transcript -Path $LogFile -Append | Out-Null
Write-Host "== Emisión de facturas en lote (conduce el emit-factusol-invoice de BoHub) ==" -ForegroundColor Cyan

if (-not $BaseUrl) {
    $BaseUrl = Read-Host "URL base de BoHub (p.ej. https://tu-dominio)"
}
$BaseUrl = $BaseUrl.TrimEnd("/")
if (-not $BaseUrl.StartsWith("http")) { throw "BaseUrl no parece una URL: $BaseUrl" }

if (-not (Test-Path $Csv)) {
    Stop-Transcript | Out-Null
    throw "No encuentro el CSV: $Csv (columnas: numero_pedido,serie)"
}

# --- 1) Login: credenciales seguras, nunca en claro ni hardcodeadas ---------
$email = Read-Host "Email BoHub"
$secPass = Read-Host "Contraseña" -AsSecureString
$plain = [System.Net.NetworkCredential]::new("", $secPass).Password

Write-Host "Autenticando en $BaseUrl ..." -ForegroundColor Gray
try {
    $loginBody = @{ email = $email; password = $plain } | ConvertTo-Json
    $login = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/auth/login" `
        -ContentType "application/json" -Body $loginBody
} catch {
    Write-Host "ERROR de login: $(Get-ErrorBody $_)" -ForegroundColor Red
    Stop-Transcript | Out-Null
    exit 1
} finally {
    $plain = $null   # descartar la contraseña en claro cuanto antes
}

if ($login.requires_2fa) {
    Write-Host "Tu usuario tiene 2FA activado; este script no completa el 2FA." -ForegroundColor Red
    Write-Host "Usa un usuario sin 2FA (con rol admin/pedidos) o desactiva el 2FA para el lote." -ForegroundColor Red
    Stop-Transcript | Out-Null
    exit 1
}
$token = $login.access_token
$headers = @{ Authorization = "Bearer $token" }
Write-Host "Login OK (token válido ~8 h)." -ForegroundColor Green

# --- 2) CSV de pedidos ------------------------------------------------------
$filas = Import-Csv -Path $Csv
if (-not $filas) { throw "El CSV está vacío." }
foreach ($f in $filas) {
    if (-not $f.numero_pedido -or -not $f.serie) {
        throw "El CSV debe tener columnas 'numero_pedido' y 'serie' con valores."
    }
}
Write-Host "CSV: $($filas.Count) pedidos a procesar." -ForegroundColor Gray

# --- 3) Mapa número de pedido → pedido de BoHub -----------------------------
# El endpoint de pedidos devuelve id (UUID), order_number (con prefijo de
# tienda, p.ej. BOPRIN-99928), invoice_status y factusol_invoice_number.
Write-Host "Cargando pedidos de BoHub ..." -ForegroundColor Gray
$lista = Invoke-RestMethod -Uri "$BaseUrl/api/erp/orders?limit=500" -Headers $headers
$porNumero = @{}
foreach ($o in $lista.items) {
    $bare = ($o.order_number -split "-")[-1]   # BOPRIN-99928 → 99928
    if (-not $porNumero.ContainsKey($bare)) { $porNumero[$bare] = $o }
}

# --- 4) Plan: resolver, comprobar idempotencia ------------------------------
$plan = @()
foreach ($f in $filas) {
    $num = "$($f.numero_pedido)".Trim()
    $serie = "$($f.serie)".Trim()
    $o = $porNumero[$num]
    $cliente = if ($o) { @($o.company_name, $o.contact_name | Where-Object { $_ }) -join " · " } else { "" }
    $yaFact = $false
    $motivo = ""
    if (-not $o) {
        $motivo = "NO ENCONTRADO en los últimos 500 pedidos (sube el límite o revisa el número)"
    } elseif ($o.factusol_invoice_number) {
        $yaFact = $true; $motivo = "ya facturado ($($o.factusol_invoice_number))"
    } elseif ($YA_FACTURADO -contains $o.invoice_status) {
        $yaFact = $true; $motivo = "ya facturado (estado $($o.invoice_status))"
    }
    $plan += [pscustomobject]@{
        numero = $num; serie = $serie; id = if ($o) { $o.id } else { $null }
        cliente = $cliente; ya_facturado = $yaFact; encontrado = [bool]$o; motivo = $motivo
    }
}

Write-Host ""
Write-Host "== PLAN ==" -ForegroundColor Cyan
$plan | Format-Table numero, serie, cliente, ya_facturado, motivo -AutoSize | Out-String | Write-Host

$aEmitir   = @($plan | Where-Object { $_.encontrado -and -not $_.ya_facturado })
$saltados  = @($plan | Where-Object { $_.ya_facturado })
$noHallado = @($plan | Where-Object { -not $_.encontrado })

Write-Host ("A emitir: {0} · Ya facturados (se saltan): {1} · No encontrados: {2}" -f `
    $aEmitir.Count, $saltados.Count, $noHallado.Count) -ForegroundColor Yellow

if ($noHallado.Count -gt 0) {
    Write-Host "Hay pedidos NO encontrados; revisa el CSV antes de aplicar." -ForegroundColor Red
}

# --- 5) Dry-run por defecto -------------------------------------------------
if (-not $Apply) {
    Write-Host ""
    Write-Host "MODO PREVISUALIZACIÓN (dry-run): no se ha emitido nada." -ForegroundColor Green
    Write-Host "Para emitir de verdad, vuelve a ejecutar con  -Apply" -ForegroundColor Green
    Stop-Transcript | Out-Null
    exit 0
}

if ($aEmitir.Count -eq 0) {
    Write-Host "No hay nada que emitir." -ForegroundColor Green
    Stop-Transcript | Out-Null
    exit 0
}

# --- 6) Confirmación antes de emitir ---------------------------------------
Write-Host ""
$resp = Read-Host ("Vas a EMITIR {0} facturas. Escribe SI para continuar" -f $aEmitir.Count)
if ($resp -ne "SI") {
    Write-Host "Cancelado. No se ha emitido nada." -ForegroundColor Yellow
    Stop-Transcript | Out-Null
    exit 0
}

# --- 7) Emisión de una en una, deteniéndose ante el primer error ------------
$emitidas = @()
$fallo = $null
foreach ($p in $aEmitir) {
    Write-Host ""
    Write-Host ("→ Pedido {0} (serie {1}) — {2}" -f $p.numero, $p.serie, $p.cliente) -ForegroundColor Cyan
    try {
        $body = @{ serie = [int]$p.serie } | ConvertTo-Json
        $emit = Invoke-RestMethod -Method Post -ContentType "application/json" -Headers $headers `
            -Uri "$BaseUrl/api/erp/orders/$($p.id)/emit-factusol-invoice" -Body $body
    } catch {
        $code = Get-HttpStatus $_
        $bodyErr = Get-ErrorBody $_
        if ($code -eq 409 -and ($bodyErr -match "already_invoiced")) {
            Write-Host "  Ya estaba facturado (409): se salta." -ForegroundColor Yellow
            $saltados += $p
            continue
        }
        $fallo = "Pedido $($p.numero): error al emitir (HTTP $code): $bodyErr"
        break
    }

    # Espera el resultado (nº de factura). El estado se resuelve cuando el
    # worker escribe la factura y BoHub guarda el CODFAC.
    $codfac = $null; $err = $null
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Seconds 2
        try {
            $st = Invoke-RestMethod -Headers $headers `
                -Uri "$BaseUrl/api/erp/orders/$($p.id)/factusol-invoice-status?job_id=$($emit.job_id)"
        } catch { continue }
        if ($st.status -eq "invoiced") { $codfac = $st.codfac; break }
        if ($st.status -eq "failed")   { $err = $st.error; break }
    }

    if ($codfac) {
        Write-Host ("  OK → factura {0}" -f $codfac) -ForegroundColor Green
        $emitidas += [pscustomobject]@{ numero = $p.numero; serie = $p.serie; codfac = $codfac }
    } elseif ($err) {
        $fallo = "Pedido $($p.numero): la emisión falló: $err"
        break
    } else {
        $fallo = "Pedido $($p.numero): sin confirmación tras 80 s (revisa BoHub antes de reintentar)."
        break
    }

    Start-Sleep -Seconds $PausaSegundos
}

# --- 8) Resumen -------------------------------------------------------------
Write-Host ""
Write-Host "== RESUMEN ==" -ForegroundColor Cyan
Write-Host ("Emitidas: {0}" -f $emitidas.Count) -ForegroundColor Green
$emitidas | Format-Table numero, serie, codfac -AutoSize | Out-String | Write-Host
Write-Host ("Saltadas (ya facturadas): {0}" -f $saltados.Count) -ForegroundColor Yellow
if ($fallo) {
    Write-Host "DETENIDO ante un error (no se siguieron emitiendo el resto):" -ForegroundColor Red
    Write-Host "  $fallo" -ForegroundColor Red
    Write-Host "Corrige y vuelve a ejecutar: los ya emitidos se saltarán solos." -ForegroundColor Red
    Write-Host "Log: $LogFile"
    Stop-Transcript | Out-Null
    exit 1
}
Write-Host "Todo el lote se procesó sin errores." -ForegroundColor Green
Write-Host "Log: $LogFile"
Stop-Transcript | Out-Null
exit 0
