"use client";

const TEXT: Record<string, { label: string; tone: string; title: string }> = {
  nacional: {
    label: "nacional · con IVA", tone: "n",
    title: "Cliente nacional: la factura lleva IVA",
  },
  intracomunitario: {
    label: "intracomunitario · exento", tone: "p",
    title: "Cliente intracomunitario (UE con VAT): la factura sale SIN IVA",
  },
  exportacion: {
    label: "exportación · exento", tone: "t",
    title: "Cliente de fuera de la UE: la factura sale SIN IVA",
  },
};

/** Etiqueta corta del régimen, para acompañar al importe en la bandeja y en
 *  la ficha: lo que hay que saber del IVA sin abrir nada. */
export function regimeLabel(regime: string | null | undefined): string | null {
  if (regime === "intracomunitario") return "exento · intracomunitario";
  if (regime === "exportacion") return "exento · exportación";
  if (regime === "nacional") return "IVA incl. · nacional";
  return null;
}

/** Pastilla del régimen de IVA del cliente. Se usa igual en la bandeja (junto
 *  al importe) y en la cabecera de la ficha, para que el IVA se vea donde se
 *  decide y no haya que abrir FACTUSOL para saberlo. */
export function RegimePill({
  regime, country,
}: {
  regime: string | null | undefined;
  country?: string | null;
}) {
  if (!regime) return null;
  const meta = TEXT[regime];
  if (!meta) return null;
  return (
    <span className={`erp-flow-pill is-${meta.tone}`} title={meta.title}>
      {country ? `${country} · ` : ""}{meta.label}
    </span>
  );
}
