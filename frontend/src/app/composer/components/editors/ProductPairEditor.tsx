"use client";

/**
 * ProductPairEditor — literal port of `function ProductPairEditor`
 * from `bomedia-v4/app-inspector.jsx` lines 744-757.
 *
 * Two product slots (product1 = left, product2 = right) each with
 * its own ProductSelect + ProductMini.
 */

import { useComposerStore } from "../../lib/store";
import type { Block, ComposerCatalog } from "../../lib/types";
import { Section } from "../InspectorPrimitives";
import { ProductMini, ProductSelect } from "./ProductEditorHelpers";

export interface ProductPairEditorProps {
  block: Block;
  catalog: ComposerCatalog;
}

export function ProductPairEditor({
  block,
  catalog,
}: ProductPairEditorProps) {
  const updateBlock = useComposerStore((s) => s.updateBlock);
  return (
    <>
      <Section title="Producto 1">
        <ProductSelect
          catalog={catalog}
          value={block.product1}
          onChange={(v) => updateBlock(block.id, { product1: v })}
          label="Producto izquierdo"
        />
        <ProductMini catalog={catalog} productId={block.product1} />
      </Section>
      <Section title="Producto 2">
        <ProductSelect
          catalog={catalog}
          value={block.product2}
          onChange={(v) => updateBlock(block.id, { product2: v })}
          label="Producto derecho"
        />
        <ProductMini catalog={catalog} productId={block.product2} />
      </Section>
    </>
  );
}
