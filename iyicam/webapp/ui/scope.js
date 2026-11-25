/* iyiCAM web: isolated groups of sheets, as the engine's core/scope.py says them (the rules the panels, the toolpath window,
 * nesting and Save G-code keep to).  An isolated group has its own layers and toolpaths (their `sheet_group`), and nothing
 * of the rest of the project is on its sheets; whatever changes a group goes through the engine (op `sheet_scope`). */

/** the isolated group a sheet is in ("" = the rest of the project) */
export const scopeOf = s => (s && s.isolated && s.group ? s.group : "");
export const isIsolated = (project, g) => !!g && project.sheets.some(s => s.group === g && s.isolated);
/** the sheets on the same side as `sheet` ("every sheet" for whatever is done from it) */
export function sheetsWith(project, sheet) {
  const side = scopeOf(sheet);
  return project.sheets.filter(s => (side ? s.group === side && s.isolated : !scopeOf(s)));
}
/** whether a layer is there on a sheet (Layer.applies) */
export function layerApplies(l, sheet) {
  if (!sheet) return true;
  if (sheet.isolated && sheet.group) return l.sheet_group === sheet.group || (Array.isArray(l.sheets) && l.sheets.includes(sheet.id));
  if (!Array.isArray(l.sheets) && !l.sheet_group) return true;
  return (Array.isArray(l.sheets) && l.sheets.includes(sheet.id)) || (!!l.sheet_group && sheet.group === l.sheet_group);
}
export const layersFor = (project, sheet) => project.layers.filter(l => layerApplies(l, sheet));
/** whether a toolpath belongs to the side of `sheet` */
export function toolpathOnSide(project, t, sheet) {
  const side = scopeOf(sheet);
  return side ? t.sheet_group === side : !isIsolated(project, t.sheet_group || "");
}
/** the groups that are not isolated (those one may choose from the rest of the project) */
export const openGroups = project => [...new Set(project.sheets.filter(s => s.group && !s.isolated).map(s => s.group))];
