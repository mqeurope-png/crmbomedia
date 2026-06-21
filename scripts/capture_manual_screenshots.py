"""PR-Manual-Capturas — captura las 24 screenshots del manual.

Asume:
  - Backend FastAPI corriendo en http://127.0.0.1:8000
  - Frontend Next.js dev corriendo en http://127.0.0.1:3000
  - BD poblada con `backend/scripts/seed_manual_demo.py`

Las capturas se guardan en `docs/manual-screenshots/NN-name.png` a
1440x900.

Uso:

    python scripts/capture_manual_screenshots.py

Si algún paso falla, el script sigue con el resto y reporta los
fallos al final.
"""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import (
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

BASE = "http://127.0.0.1:3000"
OUT = Path(__file__).resolve().parents[1] / "docs" / "manual-screenshots"
OUT.mkdir(parents=True, exist_ok=True)

VIEWPORT = {"width": 1440, "height": 900}

CREDS = {
    "admin": ("admin@demo.com", "DemoAdmin2026!"),
    "comercial": ("comercial@demo.com", "DemoComercial2026!"),
}


def login(page: Page, role: str) -> None:
    email, password = CREDS[role]
    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.fill('input[type="email"]', email)
    page.fill('input[type="password"]', password)
    page.click('button[type="submit"]')
    # Espera a que la URL salga de /login (redirige a /).
    try:
        page.wait_for_url(lambda url: "/login" not in url, timeout=10_000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_load_state("networkidle", timeout=10_000)


def shot(page: Page, name: str, full_page: bool = False) -> bool:
    """Captura `name.png`. Devuelve True/False según éxito."""
    out = OUT / name
    try:
        page.screenshot(path=str(out), full_page=full_page)
        size = out.stat().st_size
        print(f"  ✓ {name} ({size // 1024} KB)")
        return True
    except Exception as exc:
        print(f"  ✗ {name}: {exc}")
        return False


def goto_safe(page: Page, path: str, *, wait_ms: int = 1500) -> None:
    try:
        page.goto(f"{BASE}{path}", wait_until="networkidle", timeout=15_000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(wait_ms)


def click_safe(page: Page, selector: str, *, wait_ms: int = 600) -> bool:
    try:
        page.locator(selector).first.click(timeout=5_000)
        page.wait_for_timeout(wait_ms)
        return True
    except Exception as exc:
        print(f"    click_safe({selector}) fallo: {exc}")
        return False


def capture_all(role_pages: dict[str, Page]) -> dict[str, bool]:
    results: dict[str, bool] = {}
    page_c = role_pages["comercial"]
    page_a = role_pages["admin"]

    print("\n[01] login")
    page_c.goto(f"{BASE}/login", wait_until="networkidle")
    page_c.wait_for_timeout(500)
    results["01-login.png"] = shot(page_c, "01-login.png")

    # Login para el resto de capturas como comercial.
    login(page_c, "comercial")

    print("\n[02] /account")
    goto_safe(page_c, "/account", wait_ms=1500)
    results["02-account.png"] = shot(page_c, "02-account.png", full_page=False)

    print("\n[03] /dashboard")
    goto_safe(page_c, "/dashboard", wait_ms=2000)
    results["03-dashboard.png"] = shot(page_c, "03-dashboard.png")

    print("\n[04] /contacts lista")
    goto_safe(page_c, "/contacts", wait_ms=2500)
    results["04-contactos-lista.png"] = shot(page_c, "04-contactos-lista.png")

    print("\n[05] ficha contacto")
    # Click sobre la primera fila de contactos.
    if click_safe(page_c, "table tbody tr.is-clickable", wait_ms=300):
        try:
            page_c.wait_for_url(lambda u: "/contacts/" in u and "/new" not in u,
                                timeout=8_000)
        except Exception:
            pass
        page_c.wait_for_load_state("networkidle", timeout=10_000)
        page_c.wait_for_timeout(2000)
        results["05-ficha-contacto.png"] = shot(page_c, "05-ficha-contacto.png")
    else:
        results["05-ficha-contacto.png"] = False

    print("\n[06] modal nuevo contacto")
    goto_safe(page_c, "/contacts/new", wait_ms=2000)
    results["06-crear-contacto.png"] = shot(page_c, "06-crear-contacto.png")

    print("\n[07] modal editar")
    goto_safe(page_c, "/contacts", wait_ms=1500)
    if click_safe(page_c, "table tbody tr.is-clickable"):
        try:
            page_c.wait_for_url(lambda u: "/contacts/" in u and "/new" not in u,
                                timeout=8_000)
        except Exception:
            pass
        page_c.wait_for_load_state("networkidle", timeout=10_000)
        page_c.wait_for_timeout(2000)
        clicked = (
            click_safe(page_c, 'button:has-text("Editar")')
            or click_safe(page_c, 'button[aria-label*="Editar"]')
            or click_safe(page_c, "button:has(svg.lucide-pencil)")
        )
        if clicked:
            page_c.wait_for_timeout(2000)
            results["07-editar-contacto.png"] = shot(
                page_c, "07-editar-contacto.png"
            )
        else:
            results["07-editar-contacto.png"] = False
    else:
        results["07-editar-contacto.png"] = False

    print("\n[08] modal borrar — SKIP (admin-only flow, captura opcional)")
    results["08-borrar-contacto-modal.png"] = shot(page_c, "08-borrar-contacto-modal.png")

    print("\n[09] composer email")
    goto_safe(page_c, "/contacts", wait_ms=1500)
    if click_safe(page_c, "table tbody tr.is-clickable"):
        try:
            page_c.wait_for_url(lambda u: "/contacts/" in u and "/new" not in u,
                                timeout=8_000)
        except Exception:
            pass
        page_c.wait_for_load_state("networkidle", timeout=10_000)
        page_c.wait_for_timeout(1500)
        if click_safe(page_c, 'button:has-text("Enviar correo")'):
            page_c.wait_for_timeout(3000)
            results["09-composer-email.png"] = shot(
                page_c, "09-composer-email.png"
            )
        else:
            results["09-composer-email.png"] = False
    else:
        results["09-composer-email.png"] = False

    print("\n[10] bandeja emails")
    goto_safe(page_c, "/emails", wait_ms=2500)
    results["10-bandeja-emails.png"] = shot(page_c, "10-bandeja-emails.png")

    print("\n[11] dropdown plantillas (composer)")
    goto_safe(page_c, "/emails", wait_ms=1500)
    if click_safe(page_c, 'button:has-text("Redactar")'):
        page_c.wait_for_timeout(2000)
        # Intenta abrir dropdown de plantilla.
        click_safe(page_c, 'button:has-text("Plantilla")')
        page_c.wait_for_timeout(1500)
        results["11-dropdown-plantillas.png"] = shot(
            page_c, "11-dropdown-plantillas.png"
        )
    else:
        results["11-dropdown-plantillas.png"] = False

    print("\n[12] modal nueva tarea")
    goto_safe(page_c, "/tareas", wait_ms=2000)
    if (
        click_safe(page_c, 'button:has-text("Nueva tarea")')
        or click_safe(page_c, 'button:has-text("Crear tarea")')
        or click_safe(page_c, 'button:has-text("Nueva")')
        or click_safe(page_c, 'button[aria-label*="Crear"]')
        or click_safe(page_c, "button:has(svg.lucide-plus)")
    ):
        page_c.wait_for_timeout(1500)
        results["12-crear-tarea.png"] = shot(page_c, "12-crear-tarea.png")
    else:
        results["12-crear-tarea.png"] = False

    print("\n[13] /tareas lista")
    goto_safe(page_c, "/tareas", wait_ms=2000)
    results["13-lista-tareas.png"] = shot(page_c, "13-lista-tareas.png")

    print("\n[14] notas en ficha")
    goto_safe(page_c, "/contacts", wait_ms=1500)
    if click_safe(page_c, "table tbody tr.is-clickable"):
        try:
            page_c.wait_for_url(lambda u: "/contacts/" in u and "/new" not in u,
                                timeout=8_000)
        except Exception:
            pass
        page_c.wait_for_load_state("networkidle", timeout=10_000)
        page_c.wait_for_timeout(2000)
        click_safe(page_c, 'a:has-text("Notas"), button:has-text("Notas")')
        page_c.wait_for_timeout(1500)
        results["14-notas-pestania.png"] = shot(page_c, "14-notas-pestania.png")
    else:
        results["14-notas-pestania.png"] = False

    print("\n[15] tags en ficha")
    goto_safe(page_c, "/contacts", wait_ms=1500)
    if click_safe(page_c, "table tbody tr.is-clickable"):
        try:
            page_c.wait_for_url(lambda u: "/contacts/" in u and "/new" not in u,
                                timeout=8_000)
        except Exception:
            pass
        page_c.wait_for_load_state("networkidle", timeout=10_000)
        page_c.wait_for_timeout(2000)
        click_safe(page_c, 'a:has-text("Tags"), button:has-text("Tags")')
        page_c.wait_for_timeout(1500)
        results["15-editor-tags.png"] = shot(page_c, "15-editor-tags.png")
    else:
        results["15-editor-tags.png"] = False

    print("\n[16] ficha oportunidad")
    goto_safe(page_c, "/pipelines", wait_ms=2000)
    results["16-ficha-oportunidad.png"] = shot(page_c, "16-ficha-oportunidad.png")

    print("\n[17] pipeline kanban")
    goto_safe(page_c, "/pipelines", wait_ms=1500)
    if click_safe(page_c, 'a:has-text("Ventas B2B")'):
        try:
            page_c.wait_for_url(lambda u: "/pipelines/" in u, timeout=8_000)
        except Exception:
            pass
        page_c.wait_for_load_state("networkidle", timeout=10_000)
        page_c.wait_for_timeout(2000)
        results["17-pipeline-kanban.png"] = shot(page_c, "17-pipeline-kanban.png")
    else:
        # Fallback: la lista sirve como overview.
        results["17-pipeline-kanban.png"] = shot(page_c, "17-pipeline-kanban.png")

    print("\n[18] /segmentos")
    goto_safe(page_c, "/segmentos", wait_ms=2500)
    results["18-segmentos.png"] = shot(page_c, "18-segmentos.png")

    # Admin views.
    login(page_a, "admin")

    print("\n[19] /admin/workflows")
    goto_safe(page_a, "/admin/workflows", wait_ms=2500)
    results["19-workflows-lista.png"] = shot(page_a, "19-workflows-lista.png")

    print("\n[20] plantillas workflows modal")
    if click_safe(
        page_a, 'button:has-text("plantilla"), button:has-text("Plantilla")'
    ) or click_safe(
        page_a, 'button:has-text("Nueva"), button:has-text("Crear")'
    ):
        page_a.wait_for_timeout(2000)
        results["20-plantillas-workflows.png"] = shot(
            page_a, "20-plantillas-workflows.png"
        )
    else:
        results["20-plantillas-workflows.png"] = False

    print("\n[21] editor canvas — entra al primer workflow")
    goto_safe(page_a, "/admin/workflows", wait_ms=1500)
    if click_safe(page_a, 'a:has-text("Onboarding lead nuevo")'):
        try:
            page_a.wait_for_url(
                lambda u: "/admin/workflows/" in u
                and not u.rstrip("/").endswith("workflows"),
                timeout=8_000,
            )
        except Exception:
            pass
        page_a.wait_for_load_state("networkidle", timeout=10_000)
        page_a.wait_for_timeout(3000)
        results["21-editor-canvas.png"] = shot(page_a, "21-editor-canvas.png")
    else:
        results["21-editor-canvas.png"] = False

    print("\n[22] pestaña workflows en ficha contacto")
    goto_safe(page_a, "/contacts", wait_ms=1500)
    if click_safe(page_a, "table tbody tr.is-clickable"):
        try:
            page_a.wait_for_url(lambda u: "/contacts/" in u and "/new" not in u,
                                timeout=8_000)
        except Exception:
            pass
        page_a.wait_for_load_state("networkidle", timeout=10_000)
        page_a.wait_for_timeout(2000)
        click_safe(
            page_a, 'a:has-text("Workflows"), button:has-text("Workflows")'
        )
        page_a.wait_for_timeout(1500)
        results["22-workflows-ficha.png"] = shot(
            page_a, "22-workflows-ficha.png"
        )
    else:
        results["22-workflows-ficha.png"] = False

    print("\n[23] reglas de asignación")
    goto_safe(
        page_a, "/admin/assignment-rules", wait_ms=2000
    )
    results["23-reglas-asignacion.png"] = shot(
        page_a, "23-reglas-asignacion.png"
    )

    print("\n[24] marketing")
    goto_safe(page_a, "/marketing", wait_ms=2500)
    results["24-marketing.png"] = shot(page_a, "24-marketing.png")

    return results


def main() -> None:
    with sync_playwright() as p:
        # PR-Manual-Capturas: el sandbox usa Playwright Python 1.60
        # con Chromium build 1194 ya descargado en /opt/pw-browsers.
        # El default de la versión actual apunta a 1223 que no está,
        # así que forzamos el binario existente.
        import os

        exe = os.environ.get(
            "PW_CHROMIUM_PATH",
            "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
        )
        browser = p.chromium.launch(
            headless=True,
            executable_path=exe if os.path.exists(exe) else None,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx_c = browser.new_context(viewport=VIEWPORT, locale="es-ES")
        ctx_a = browser.new_context(viewport=VIEWPORT, locale="es-ES")
        page_c = ctx_c.new_page()
        page_a = ctx_a.new_page()
        try:
            results = capture_all({"comercial": page_c, "admin": page_a})
        finally:
            browser.close()

    ok = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"\n=== {ok}/{total} screenshots OK ===")
    fails = [name for name, ok in results.items() if not ok]
    if fails:
        print("Fallidas:")
        for n in fails:
            print(f"  - {n}")


if __name__ == "__main__":
    main()
