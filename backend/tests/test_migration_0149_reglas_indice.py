"""Migración 20261028_0149 — reglas del índice de las webs por idioma.

Sobre la tabla que siembra la 0148 (0147 → 0148, como en producción) se
meten reglas `categoria_fuera` de la web `fluxlasers` (el parche a mano del
27/10), una de todas las webs y una de otra web, y se sube a la 0149: solo se
van las de `fluxlasers` (y el registro dice cuáles, una a una), entra el
WhiteRIP (493 de artisjet-eu) una vez aunque se suba dos veces, y bajar quita
solo el que puso la 0149: uno que ya estaba se queda.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.core.config import get_settings
from app.services.webs import reglas_partida

BACKEND_ROOT = Path(__file__).resolve().parents[1]
#: Unos cuantos del parche de producción (nombres traducidos exactos).
PARCHE_FLUX = ("Spare Parts", "Beambox Spare Parts", "Ersatzteile", "Ador Onderdelen",
               "Pièces détachées Beamo")


@pytest.fixture()
def alembic_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path / 'reglas.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    get_settings.cache_clear()
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    old_cwd = os.getcwd()
    os.chdir(BACKEND_ROOT)
    try:
        yield cfg, db_url
    finally:
        os.chdir(old_cwd)
        get_settings.cache_clear()


def _regla(c, tipo: str, patron: str, sitio: str | None, posicion: int = 90,  # noqa: ANN001
           nota: str | None = None) -> None:
    ahora = datetime.now(UTC)
    c.execute(text(
        "INSERT INTO web_indice_reglas (tipo, patron, sitio, activo, nota, posicion, "
        "created_at, updated_at) VALUES (:t, :p, :s, 1, :n, :pos, :a, :a)"),
        {"t": tipo, "p": patron, "s": sitio, "n": nota, "pos": posicion, "a": ahora})


def _reglas(engine) -> list[tuple[str, str | None, str]]:  # noqa: ANN001
    with engine.connect() as c:
        return [(r.tipo, r.sitio, r.patron) for r in c.execute(text(
            "SELECT tipo, sitio, patron FROM web_indice_reglas ORDER BY tipo, posicion, id"))]


def test_quita_el_parche_de_flux_anade_el_whiterip_y_baja_limpia(
        alembic_setup, capsys: pytest.CaptureFixture[str]) -> None:
    cfg, db_url = alembic_setup
    command.stamp(cfg, "20261027_0147")
    command.upgrade(cfg, "20261027_0148")
    engine = create_engine(db_url)
    sembradas = _reglas(engine)
    with engine.begin() as c:
        for posicion, nombre in enumerate(PARCHE_FLUX):
            _regla(c, "categoria_fuera", nombre, "fluxlasers", 20 + posicion)
        _regla(c, "categoria_fuera", "Ersatzteile", None)            # de todas las webs
        _regla(c, "categoria_fuera", "Pièces détachées", "artisjet-eu")
        _regla(c, "producto_excluir_id", "3949", "fluxlasers")       # de FLUX, pero no de categoría
        c.execute(text("INSERT INTO web_indice (id, sitio, dominio, url, tipo, wp_id, idioma, "
                       "titulo, texto, visto_en, ido_en, created_at, updated_at) VALUES "
                       "('x', 'fluxlasers', 'fluxlasers.eu', 'https://fluxlasers.eu/en/p/', "
                       "'producto', 15580, 'en', 'BB2 Water Pump', 't', :a, :a, :a, :a)"),
                  {"a": datetime.now(UTC)})
    engine.dispose()

    capsys.readouterr()
    command.upgrade(cfg, "20261028_0149")
    # El registro de la migración: `caplog` no lo ve (el `fileConfig` de
    # `alembic/env.py` cambia los manejadores), sale por la consola de alembic.
    registro = capsys.readouterr().err
    engine = create_engine(db_url)
    despues = _reglas(engine)
    # Fuera solo las de categoría de fluxlasers; lo demás, igual.
    assert not [r for r in despues if r[0] == "categoria_fuera" and r[1] == "fluxlasers"]
    assert ("categoria_fuera", None, "Ersatzteile") in despues
    assert ("categoria_fuera", "artisjet-eu", "Pièces détachées") in despues
    assert ("producto_excluir_id", "fluxlasers", "3949") in despues
    assert [r for r in sembradas if r not in despues] == []
    assert despues.count(("producto_incluir_id", "artisjet-eu", "493")) == 1
    assert "0149: 5 reglas categoria_fuera de fluxlasers borradas" in registro
    # Cada patrón borrado, en el registro del despliegue: el parche se puede
    # reponer a mano desde ahí.
    for nombre in PARCHE_FLUX:
        assert f"0149: borrada la regla categoria_fuera de fluxlasers: {nombre!r}" in registro
    assert registro.count("0149: borrada la regla") == len(PARCHE_FLUX)
    with engine.connect() as c:
        nota, posicion = c.execute(text(
            "SELECT nota, posicion FROM web_indice_reglas WHERE patron = '493'")).one()
        # No toca el índice: lo marcado ido el 27/10 sigue ido.
        assert c.execute(text("SELECT ido_en FROM web_indice WHERE id = 'x'")).scalar()
    assert nota == "WhiteRIP: software colgado de «Autres pièces de rechange» (migración 0149)"
    assert posicion == 1                                  # tras el 477 (posición 0)
    engine.dispose()

    # Tras la 0148 + 0149, la tabla trae justo las reglas de partida del código.
    partida = sorted(
        [("pagina_excluir_slug", None, p) for p, _ in reglas_partida.PAGINAS_FUERA]
        + [("categoria_fuera", None, p) for p, _ in reglas_partida.CATEGORIAS_FUERA]
        + [("producto_excluir_id", s, str(i)) for s, i, _ in reglas_partida.PRODUCTOS_FUERA]
        + [("producto_incluir_id", s, str(i)) for s, i, _ in reglas_partida.PRODUCTOS_DENTRO],
        key=str)
    extra = {("categoria_fuera", None, "Ersatzteile"),
             ("categoria_fuera", "artisjet-eu", "Pièces détachées"),
             ("producto_excluir_id", "fluxlasers", "3949")}
    assert sorted([r for r in despues if r not in extra], key=str) == partida

    # Bajar quita el WhiteRIP (el parche de FLUX no vuelve).
    command.downgrade(cfg, "20261027_0148")
    engine = create_engine(db_url)
    bajada = _reglas(engine)
    assert ("producto_incluir_id", "artisjet-eu", "493") not in bajada
    assert not [r for r in bajada if r[1] == "fluxlasers" and r[0] == "categoria_fuera"]
    assert ("producto_incluir_id", "artisjet-eu", "477") in bajada
    # Subir otra vez con el 493 ya puesto a mano: sigue habiendo uno.
    with engine.begin() as c:
        _regla(c, "producto_incluir_id", "493", "artisjet-eu", 7)
    engine.dispose()
    command.upgrade(cfg, "20261028_0149")
    engine = create_engine(db_url)
    assert _reglas(engine).count(("producto_incluir_id", "artisjet-eu", "493")) == 1
    engine.dispose()


@pytest.mark.parametrize("nota", [
    None, "WhiteRIP: software colgado de «Autres pièces de rechange»"])
def test_bajar_no_quita_un_493_que_ya_estaba(
        alembic_setup, capsys: pytest.CaptureFixture[str], nota: str | None) -> None:
    """Si el 493 ya estaba (a mano, con otra nota o con la de las reglas de
    partida), la 0149 no lo pone y bajar no lo quita: solo quita la fila con
    la nota propia de la 0149."""
    cfg, db_url = alembic_setup
    command.stamp(cfg, "20261027_0147")
    command.upgrade(cfg, "20261027_0148")
    engine = create_engine(db_url)
    with engine.begin() as c:
        _regla(c, "producto_incluir_id", "493", "artisjet-eu", 7, nota)
    engine.dispose()
    capsys.readouterr()
    command.upgrade(cfg, "20261028_0149")
    assert "0149: la regla producto_incluir_id 493 de artisjet-eu ya estaba" in (
        capsys.readouterr().err)
    command.downgrade(cfg, "20261027_0148")
    engine = create_engine(db_url)
    with engine.connect() as c:
        filas = c.execute(text(
            "SELECT nota, posicion FROM web_indice_reglas WHERE tipo = 'producto_incluir_id' "
            "AND patron = '493' AND sitio = 'artisjet-eu'")).all()
    engine.dispose()
    assert [(f.nota, f.posicion) for f in filas] == [(nota, 7)]
