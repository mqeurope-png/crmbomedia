"use client";

/**
 * ProductTrioEditor — literal port of `function ProductTrioEditor`
 * from `bomedia-v4/app-inspector.jsx` lines 759-776.
 *
 * Three product slots (product1 / product2 / product3) each with its
 * own ProductSelect + ProductMini.
 */

import { useComposerStore } from "../../lib/store";
import type { Block, ComposerCatalog } from "../../lib/types";
import { Section } from "../InspectorPrimitives";
import { ProductMini, ProductSelect } from "./ProductEditorHelpers";

export interface ProductTrioEditorProps {
  block: Block;
  catalog: ComposerCatalog;
}

export function ProductTrioEditor({
  block,
  catalog,
}: ProductTrioEditorProps) {
  const updateBlock = useComposerStore((s) => s.updateBlock);
  return (
    <>
      <Section title="Producto 1">
        <ProductSelect
          catalog={catalog}
          value={block.product1}
          onChange={(v) => updateBlock(block.id, { product1: v })}
        />
        <ProductMini catalog={catalog} productId={block.product1} />
      </Section>
      <Section title="Producto 2">
        <ProductSelect
          catalog={catalog}
          value={block.product2}
          onChange={(v) => updateBlock(block.id, { product2: v })}
        />
        <ProductMini catalog={catalog} productId={block.product2} />
      </Section>
      <Section title="Producto 3">
        <ProductSelect
          catalog={catalog}
          value={block.product3}
          onChange={(v) => updateBlock(block.id, { product3: v })}
        />
        <ProductMini catalog={catalog} productId={block.product3} />
      </Section>
    </>
  );
}
