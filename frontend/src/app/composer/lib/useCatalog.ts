"use client";

/** Single-call hook that fetches `/api/composer/catalog` on mount and
 * exposes `{catalog, loading, error}`. Cached at module scope so the
 * sidebar / canvas / inspector don't each issue their own request. */
import { useEffect, useState } from "react";

import { getCatalog } from "./composerApi";
import type { ComposerCatalog } from "./types";

let cachedCatalog: ComposerCatalog | null = null;
let inFlight: Promise<ComposerCatalog> | null = null;

export interface UseCatalogResult {
  catalog: ComposerCatalog | null;
  loading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
}

export function useCatalog(): UseCatalogResult {
  const [catalog, setCatalog] = useState<ComposerCatalog | null>(cachedCatalog);
  const [loading, setLoading] = useState<boolean>(cachedCatalog === null);
  const [error, setError] = useState<string | null>(null);

  const fetchOnce = async (): Promise<void> => {
    setLoading(true);
    try {
      // `getCatalog()` is typed against the Fase-1 schema in
      // `composerApi.ts`; the runtime shape matches the Fase-2
      // `ComposerCatalog` in `types.ts`. Cast through `unknown` so
      // the type narrowing on `i18n` doesn't trip us up.
      if (!inFlight)
        inFlight = getCatalog() as unknown as Promise<ComposerCatalog>;
      const next = await inFlight;
      cachedCatalog = next;
      setCatalog(next);
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
    } finally {
      inFlight = null;
      setLoading(false);
    }
  };

  useEffect(() => {
    if (cachedCatalog !== null) {
      setCatalog(cachedCatalog);
      setLoading(false);
      return;
    }
    void fetchOnce();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refetch = async (): Promise<void> => {
    cachedCatalog = null;
    inFlight = null;
    await fetchOnce();
  };

  return { catalog, loading, error, refetch };
}

/** Test-only — reset the module cache between cases. */
export function __resetCatalogCache(): void {
  cachedCatalog = null;
  inFlight = null;
}
