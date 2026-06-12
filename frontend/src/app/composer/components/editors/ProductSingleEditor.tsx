"use client";

/**
 * ProductSingleEditor — literal port of `function ProductSingleEditor`
 * from `bomedia-v4/app-inspector.jsx` lines 735-742.
 *
 * One product slot (block.product1) with a ProductSelect dropdown +
 * a ProductMini preview below it. No overrides UI — the original
 * single-product editor is intentionally minimal; the legacy
 * `ProductEditor` (line 192) handles overrides + visibility + CTA
 * but is bound to the older `type: 'product'` block, not
 * `type: 'product_single'`.
 */

import { useComposerStore } from "../../lib/store";
import type { Block, ComposerCatalog } from "../../lib/types";
import { Section } from "../InspectorPrimitives";
import { ProductMini, ProductSelect } from "./ProductEditorHelpers";

export interface ProductSingleEditorProps {
  block: Block;
  catalog: ComposerCatalog;
}

export function ProductSingleEditor({
  block,
  catalog,
}: ProductSingleEditorProps) {
  const updateBlock = useComposerStore((s) => s.updateBlock);
  return (
    <Section title="Producto">
      <ProductSelect
        catalog={catalog}
        value={block.product1}
        onChange={(v) => updateBlock(block.id, { product1: v })}
        label="Producto"
      />
      <ProductMini catalog={catalog} productId={block.product1} />
    </Section>
  );
}
