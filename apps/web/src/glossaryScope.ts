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

/** Reject late old-scope reads even if their request counter is newer. */
export function shouldApplyGlossaryResponse(
  activeScope: string,
  requestedScope: string,
  requestedSequence: number,
  latestSequence: number,
): boolean {
  return activeScope === requestedScope && requestedSequence === latestSequence;
}
