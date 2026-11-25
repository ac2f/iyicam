/* iyiCAM web: the job: backplot, job report, packages and templates, post-processors, stock of plates, plugins, Python console, keyboard shortcuts.
 * `install()` runs once the window is built: it registers the menu items it makes work (`A.register(id, {run, enabled, checked})`),
 * drawing tools (`app.extraTools[name] = {down, move, hover, up}`) and what it draws over the drawing (`app.overlays`). */
import * as A from "./actions.js";
import { app } from "./state.js";

export function install() {
}
