"use client";

/** PR-Frontend-Workflows-Pipelines-Templates. Helper visual común para
 *  workflows + pipelines per-user. Lee `is_mine` / `is_global` que el
 *  backend computa contra el current_user en cada respuesta. Si un
 *  admin es owner de un recurso global, salen ambos badges. */
type Props = {
  isMine: boolean;
  isGlobal: boolean;
  size?: "sm" | "md";
};

export function ResourceVisibilityBadge({
  isMine,
  isGlobal,
  size = "sm",
}: Props) {
  const cls = size === "md" ? "rv-badge" : "rv-badge rv-badge-sm";
  return (
    <span className="rv-badges">
      {isMine ? (
        <span className={`${cls} rv-badge-mine`} title="Tuyo">
          Mío
        </span>
      ) : null}
      {isGlobal ? (
        <span className={`${cls} rv-badge-team`} title="Del equipo">
          Equipo
        </span>
      ) : null}
    </span>
  );
}
