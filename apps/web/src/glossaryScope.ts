/** Scope-tagged records: do not display late responses from another project. */
export interface GlossaryProjection<Row> {
  scope: string;
  rows: Row[];
}
export function visibleGlossaryRows<Row>(
  projection: GlossaryProjection<Row>,
  activeScope: string,
): Row[] {
  return projection.scope === activeScope ? projection.rows : [];
}
