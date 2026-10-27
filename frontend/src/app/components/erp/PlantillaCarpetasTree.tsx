"use client";

import { ChevronDown, ChevronRight, Folder, Lock, Star } from "lucide-react";
import { useState } from "react";
import type { PlantillaCarpetaNode } from "../../lib/erpApi";
import { CARPETA_SIN, CARPETA_TODAS } from "./plantillasUi";

/** Árbol de carpetas de las plantillas de documento, con el mismo aspecto y
 *  las mismas clases que el de las plantillas de correo (`/emails/
 *  plantillas`): «Todas», «Sin carpeta» y las carpetas (hasta 3 niveles), con
 *  el número de plantillas y la ★ de carpeta por defecto del usuario. */
export function PlantillaCarpetasTree({
  nodes,
  selected,
  onSelect,
  onToggleDefault,
  showSinCarpeta = true,
}: {
  nodes: PlantillaCarpetaNode[];
  /** Id de carpeta, `CARPETA_TODAS` o `CARPETA_SIN`. */
  selected: string;
  onSelect: (key: string) => void;
  /** Sin él no se pinta la ★ (el selector solo la enseña). */
  onToggleDefault?: (node: PlantillaCarpetaNode) => void;
  /** Un usuario con acceso acotado no tiene plantillas en la raíz. */
  showSinCarpeta?: boolean;
}) {
  return (
    <nav aria-label="Carpetas de plantillas">
      <ul className="et-tree et-tree-root">
        <RaizItem label="Todas" active={selected === CARPETA_TODAS}
                  onClick={() => onSelect(CARPETA_TODAS)} />
        {showSinCarpeta ? (
          <RaizItem label="Sin carpeta" active={selected === CARPETA_SIN}
                    onClick={() => onSelect(CARPETA_SIN)} />
        ) : null}
      </ul>
      <Ramas nodes={nodes} selected={selected} onSelect={onSelect}
             onToggleDefault={onToggleDefault} depth={0} />
    </nav>
  );
}

function RaizItem({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <li className="et-tree-item">
      <div className={`et-tree-row${active ? " is-selected" : ""}`} style={{ paddingLeft: "8px" }}>
        <span className="et-tree-toggle is-empty" aria-hidden />
        <button type="button" className="et-tree-label" aria-pressed={active} onClick={onClick}>
          <Folder size={14} aria-hidden />
          <span>{label}</span>
        </button>
      </div>
    </li>
  );
}

function Ramas({
  nodes, selected, onSelect, onToggleDefault, depth,
}: {
  nodes: PlantillaCarpetaNode[];
  selected: string;
  onSelect: (key: string) => void;
  onToggleDefault?: (node: PlantillaCarpetaNode) => void;
  depth: number;
}) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  if (nodes.length === 0) return null;
  return (
    <ul className="et-tree">
      {nodes.map((node) => {
        const isOpen = open[node.id] ?? depth < 1;
        const hasChildren = node.children.length > 0;
        const isSelected = selected === node.id;
        const acotada = (node.acceso_user_ids?.length ?? 0) > 0;
        return (
          <li key={node.id} className="et-tree-item">
            <div className={`et-tree-row${isSelected ? " is-selected" : ""}`}
                 style={{ paddingLeft: `${depth * 14 + 8}px` }}>
              {hasChildren ? (
                <button type="button" className="et-tree-toggle"
                        aria-label={isOpen ? `Contraer ${node.nombre}` : `Expandir ${node.nombre}`}
                        onClick={() => setOpen((prev) => ({ ...prev, [node.id]: !isOpen }))}>
                  {isOpen ? <ChevronDown size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />}
                </button>
              ) : (
                <span className="et-tree-toggle is-empty" aria-hidden />
              )}
              <button type="button" className="et-tree-label" aria-pressed={isSelected}
                      aria-label={`Carpeta ${node.nombre}`}
                      onClick={() => onSelect(node.id)}>
                <Folder size={14} aria-hidden />
                <span>{node.nombre}</span>
                {acotada ? (
                  <Lock size={11} className="et-visibility-icon"
                        aria-label={`Acceso acotado a ${node.acceso_user_ids?.length} usuario(s)`} />
                ) : null}
                <span className="et-tree-count">{node.num_plantillas}</span>
              </button>
              {onToggleDefault ? (
                <button type="button"
                        className={`et-folder-default-star${node.es_defecto ? " is-default" : ""}`}
                        title={node.es_defecto
                          ? "Quitar como carpeta por defecto"
                          : "Carpeta por defecto: el selector se abre aquí y aquí se guardan las nuevas"}
                        aria-label={node.es_defecto
                          ? `Quitar ${node.nombre} como carpeta por defecto`
                          : `Marcar ${node.nombre} como carpeta por defecto`}
                        aria-pressed={node.es_defecto}
                        onClick={(e) => {
                          e.stopPropagation();
                          onToggleDefault(node);
                        }}>
                  <Star size={13} aria-hidden fill={node.es_defecto ? "currentColor" : "none"} />
                </button>
              ) : node.es_defecto ? (
                <span className="et-folder-default-star is-default" title="Tu carpeta por defecto">
                  <Star size={13} aria-label="Carpeta por defecto" fill="currentColor" />
                </span>
              ) : null}
            </div>
            {hasChildren && isOpen ? (
              <Ramas nodes={node.children} selected={selected} onSelect={onSelect}
                     onToggleDefault={onToggleDefault} depth={depth + 1} />
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
