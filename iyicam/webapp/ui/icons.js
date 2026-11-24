/* iyiCAM web: the icons of the tool bars, drawn as the desktop's (a line drawing in the colour of the text). */
import { h } from "./dom.js";

const P = {
  new: "M6 3h8l4 4v14H6z M14 3v4h4 M12 11v6 M9 14h6",
  open: "M3 6h6l2 2h10v11H3z",
  save: "M5 3h11l3 3v15H5z M8 3v5h7V3 M8 21v-7h8v7",
  undo: "M9 6 4 11l5 5 M4 11h9a6 6 0 0 1 0 12h-2",
  redo: "M15 6l5 5-5 5 M20 11h-9a6 6 0 0 0 0 12h2",
  zoomext: "M4 9V4h5 M15 4h5v5 M20 15v5h-5 M9 20H4v-5 M9 9h6v6H9z",
  nest: "M4 4h7v7H4z M13 4h7v7h-7z M4 13h7v7H4z M13 13h7v7h-7z",
  newtp: "M3 5h14v10H3z M14 15l4 5 M10 15v6 M6 21h8",
  material: "M4 5h16 M4 19h16 M12 5v14 M9 8l3-3 3 3 M9 16l3 3 3-3",
  cutorder: "M4 6h2 M4 12h2 M4 18h2 M9 6h11 M9 12h11 M9 18h11",
  recalc: "M20 12a8 8 0 1 1-2.5-5.8 M20 4v6h-6",
  simulate: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M10 8.5v7l6-3.5z",
  gcode: "M6 3h8l4 4v14H6z M14 3v4h4 M9 13h6 M9 17h4",
  select: "M5 3l14 8-6.5 2L10 20z",
  nodes: "M4 19c3-9 6-9 8-4s6 2 8-9 M3 18h3v3H3z M18 4h3v3h-3z",
  polyline: "M3 19 8 7l6 9 7-12",
  line: "M4 20 20 4",
  rect: "M4 6h16v12H4z",
  circle: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z",
  ellipse: "M12 6c5 0 9 2.7 9 6s-4 6-9 6-9-2.7-9-6 4-6 9-6z",
  polygon: "M12 3l8 4.5v9L12 21l-8-4.5v-9z",
  star: "M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z",
  arc: "M4 19a10 10 0 0 1 16 0",
  text: "M5 20 12 4l7 16 M8 14h8",
  measure: "M3 15 15 3l6 6L9 21z M8 8l2.5 2.5 M11 5l2.5 2.5 M5 11l2.5 2.5",
  trim: "M12 3v18 M3 15h18 M7 10l-4 5 4 5",
  startpoint: "M12 10a3 3 0 1 0 0 6 3 3 0 0 0 0-6z M12 3v4 M5 21h14",
  plus: "M12 5v14 M5 12h14",
  minus: "M5 12h14",
  up: "M12 6l7 12H5z",
  down: "M12 18 5 6h14z",
  stop: "M6 6h12v12H6z",
  indent: "M5 5v9h10 M11 10l4 4-4 4",
  dashed: "M4 4h16v16H4z",
  edit: "M4 20l1-4L16 5l3 3L8 19z M14 7l3 3",
  copy: "M9 9h11v11H9z M4 4h11v3 M4 4v11h3",
  close: "M6 6l12 12 M18 6 6 18",
  chevron: "M6 9l6 6 6-6",
  folder: "M3 6h6l2 2h10v11H3z",
  file: "M6 3h8l4 4v14H6z M14 3v4h4",
  menu: "M4 6h16 M4 12h16 M4 18h16",
  panel: "M3 4h18v16H3z M9 4v16",
  panelr: "M3 4h18v16H3z M15 4v16",
  float: "M8 8h10v10H8z M5 5h8v2 M5 5v8h2",
  gear: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z M12 3v3 M12 18v3 M3 12h3 M18 12h3 M5.6 5.6l2.1 2.1 M16.3 16.3l2.1 2.1 M18.4 5.6l-2.1 2.1 M7.7 16.3l-2.1 2.1",
};
const FILLED = new Set(["select", "up", "down", "stop"]);

/** An inline SVG icon by name (`currentColor`, 24-unit box). */
export function icon(name, size = 20) {
  const filled = FILLED.has(name);
  return h("svg", { viewBox: "0 0 24 24", width: size, height: size, class: "icon", fill: filled ? "currentColor" : "none",
    stroke: "currentColor", "stroke-width": "1.9", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" },
    h("path", { d: P[name] || P.file, "stroke-dasharray": name === "dashed" ? "3 3" : null }),
    name === "startpoint" ? h("circle", { cx: 12, cy: 13, r: 3, fill: "#1a9641", stroke: "none" }) : null);
}
