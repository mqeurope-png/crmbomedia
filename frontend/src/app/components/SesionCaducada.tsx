"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  EVENTO_SESION_CADUCADA,
  logout,
  sesionCaducada,
  sesionRenovadaEnOtraPestana,
} from "../lib/api";

/** Cada cuánto se mira, con el aviso a la vista, si se ha entrado de nuevo en
 *  otra pestaña (por si esta sigue a la vista y no recibe foco). */
const REVISAR_CADA_MS = 5000;

/** El aviso de «sesión caducada». La capa de llamadas (`lib/api.ts`) lanza el
 *  evento `bohub:sesion-caducada` la primera vez que una llamada autenticada
 *  recibe un 401; aquí se pinta una franja fija arriba que no se cierra sin
 *  querer (no hay «×»): lo único que hace es «Entrar de nuevo», que borra el
 *  token local y lleva a /login. No se navega sola: lo que haya escrito sin
 *  guardar en la pantalla se perdería, y así se puede copiar antes de salir.
 *  Vive en AppShell solo en las rutas con sesión; al pasar por /login se
 *  desmonta y su estado se va con él. Si se entra de nuevo desde OTRA pestaña,
 *  se quita solo (ver el efecto multipestaña). */
export function SesionCaducada() {
  const router = useRouter();
  // El 401 pudo llegar antes de montarse el aviso (p. ej. mientras AppShell
  // aún no pintaba la pantalla): se mira la marca del módulo al empezar.
  const [caducada, setCaducada] = useState(() => sesionCaducada());
  const [saliendo, setSaliendo] = useState(false);

  useEffect(() => {
    const alCaducar = () => setCaducada(true);
    window.addEventListener(EVENTO_SESION_CADUCADA, alCaducar);
    return () => window.removeEventListener(EVENTO_SESION_CADUCADA, alCaducar);
  }, []);

  // Multipestaña: si se entra de nuevo en otra pestaña (la cookie es común),
  // la franja de esta mentiría y no se puede cerrar. Al volver a la pestaña
  // (foco / visibilidad) y cada poco mientras se ve, si el token guardado ya
  // no es el que caducó se olvida el aviso y se quita.
  useEffect(() => {
    if (!caducada) return;
    const revisar = () => {
      if (sesionRenovadaEnOtraPestana()) setCaducada(false);
    };
    window.addEventListener("focus", revisar);
    document.addEventListener("visibilitychange", revisar);
    const intervalo = window.setInterval(revisar, REVISAR_CADA_MS);
    return () => {
      window.removeEventListener("focus", revisar);
      document.removeEventListener("visibilitychange", revisar);
      window.clearInterval(intervalo);
    };
  }, [caducada]);

  if (!caducada) return null;

  function entrarDeNuevo() {
    setSaliendo(true);
    void logout().finally(() => router.push("/login"));
  }

  return (
    <div className="sesion-caducada" role="alert">
      <p className="sesion-caducada-texto">
        <strong>Tu sesión ha caducado.</strong> Lo que ves puede no estar al día y lo que guardes no
        se guardará. Vuelve a entrar.
        <span className="sesion-caducada-nota"> Si tenías algo escrito sin guardar, cópialo antes.</span>
      </p>
      <button type="button" className="button small" onClick={entrarDeNuevo} disabled={saliendo}>
        {saliendo ? "Saliendo…" : "Entrar de nuevo"}
      </button>
    </div>
  );
}
