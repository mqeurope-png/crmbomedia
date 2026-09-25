"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** El antiguo «modo trabajo» a pantalla completa de un pedido ya no existe:
 *  preparar y embalar se hace en un MODAL sobre la propia Cola SAT. Un enlace
 *  o marcador viejo a `/erp/sat/<id>` vuelve a la cola. */
export default function SatOrderWorkRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/erp/sat");
  }, [router]);
  return null;
}
