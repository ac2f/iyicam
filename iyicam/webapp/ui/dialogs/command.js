/* iyiCAM web: the form of a vector command (a Param list, as the desktop's CommandDialog builds it). */
import { formDialog } from "./common.js";

export function commandForm(cmd, doc) {
  const sheet = doc.sheet;
  // the defaults that depend on the drawing: nothing here, the engine knows the sheet when the value is left empty
  void sheet;
  return formDialog({ title: cmd.label.replace(/\.\.\.$/, ""), params: cmd.params, width: 420, ok: "OK", remember: "cmd." + cmd.id,
    description: cmd.tooltip || "", collapse: cmd.params.some(p => p.advanced) });
}
