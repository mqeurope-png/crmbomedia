"""Reparte los ficheros de tests de pytest en N trozos equilibrados.

El CI corre la suite del backend en varios trabajos en paralelo (uno por
trozo) en vez de uno solo de media hora. Cada trabajo recoge la colección
completa (`pytest --collect-only`, unos segundos), cuenta los tests de cada
fichero y empaqueta los ficheros en N cajas por tamaño, de mayor a menor, de
forma determinista: todos los trabajos calculan el mismo reparto y cada
fichero cae en exactamente un trozo. Se imprime la lista de ficheros del
trozo pedido, uno por línea, para pasársela a pytest.

Uso (desde `backend/`):

    python ../.github/scripts/pytest_shard.py --shards 8 --index 3 | xargs python -m pytest

No toca la aplicación ni la configuración de pytest: solo decide qué ficheros
corre cada trabajo. La cobertura es la misma, repartida.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter


def contar_tests_por_fichero(salida_collect: str) -> Counter[str]:
    """`tests/x.py::TestY::test_z` → `{tests/x.py: n}`. Ignora el resto de
    líneas de la colección (avisos, el resumen final)."""
    conteo: Counter[str] = Counter()
    for linea in salida_collect.splitlines():
        linea = linea.strip()
        if "::" not in linea:
            continue
        conteo[linea.split("::", 1)[0]] += 1
    return conteo


def repartir(conteo: dict[str, int], shards: int) -> list[list[str]]:
    """Empaquetado codicioso: los ficheros de mayor a menor (desempate por
    nombre) van cayendo en la caja menos cargada."""
    cajas: list[list[str]] = [[] for _ in range(shards)]
    carga = [0] * shards
    for fichero, n in sorted(conteo.items(), key=lambda kv: (-kv[1], kv[0])):
        i = min(range(shards), key=lambda k: (carga[k], k))
        cajas[i].append(fichero)
        carga[i] += n
    return [sorted(caja) for caja in cajas]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--shards", type=int, required=True, help="Número de trozos (N).")
    parser.add_argument("--index", type=int, required=True, help="Trozo a imprimir, de 1 a N.")
    args = parser.parse_args(argv)
    if args.shards < 1 or not 1 <= args.index <= args.shards:
        parser.error("--index debe estar entre 1 y --shards")

    proceso = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        capture_output=True, text=True, check=False,
    )
    conteo = contar_tests_por_fichero(proceso.stdout)
    if proceso.returncode not in (0, 5) or not conteo:
        sys.stderr.write(proceso.stdout[-4000:])
        sys.stderr.write(proceso.stderr[-4000:])
        sys.stderr.write(
            f"\nLa colección de pytest falló (código {proceso.returncode}) o no encontró tests.\n"
        )
        return 1
    trozo = repartir(conteo, args.shards)[args.index - 1]
    total = sum(conteo[f] for f in trozo)
    sys.stderr.write(
        f"trozo {args.index}/{args.shards}: {len(trozo)} ficheros, {total} tests "
        f"de {sum(conteo.values())}\n"
    )
    for fichero in trozo:
        print(fichero)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
