/* iyiCAM web: the forms of the draw features: Text, Text on Curve and Trace Bitmap (the desktop's TextDialog and the two forms of
 * main_window.py, with the same fields).  The work itself is the engine's (`text`, `text_on_curve`, `trace_bitmap`). */
import { h } from "../dom.js";
import { app } from "../state.js";
import { formDialog } from "./common.js";
import { pickDeviceFiles, toB64 } from "../files.js";

let fontList = null;
/** the fonts the engine has: [[name, name], ...] for a choice */
export async function fontChoices() {
  if (!fontList) {
    try { const r = await app.engine.call("fonts", {}); fontList = r.families.map(n => [n, n]); } catch { fontList = [["DejaVu Sans", "DejaVu Sans"]]; }
  }
  return fontList;
}

/** a button that takes a font file of the device (the engine reads .ttf / .otf); `state.file` holds {name, b64} */
function fontFilePicker(state) {
  const label = h("span", { class: "muted" }, "the fonts above come with iyiCAM");
  const btn = h("button", { type: "button", class: "btn small", onclick: async () => {
    if (state.file) { state.file = null; label.textContent = "the fonts above come with iyiCAM"; btn.textContent = "Use a font file…"; return; }
    const f = (await pickDeviceFiles(".ttf,.otf,.ttc"))[0];
    if (!f) return;
    state.file = { name: f.name, b64: toB64(f.bytes) };
    label.textContent = f.name; btn.textContent = "Back to the fonts above";
  } }, "Use a font file…");
  return h("div", { class: "fontfile" }, btn, " ", label);
}

const FONT_NOTE = "Letters are taken from the font. Bold and italic are made from the font (italic is slanted).";

/** the Text tool's dialog: resolves the values of the op `text` (text, font, height, bold, italic, align, spacing, font_b64) or null */
export async function textDialog() {
  const fonts = await fontChoices();
  const own = { file: null };
  const params = [
    { name: "text", label: "Text", kind: "text", default: "iyiCAM" },
    { name: "font", label: "Font", kind: "choice", choices: fonts, default: fonts[0][0] },
    { name: "height", label: "Capital height", kind: "length", default: 30, min: 0.1, max: 10000 },
    { name: "spacing", label: "Extra letter spacing", kind: "length", default: 0, min: -1000, max: 1000 },
    { name: "bold", label: "Bold", kind: "bool", default: false },
    { name: "italic", label: "Italic", kind: "bool", default: false },
    { name: "align", label: "Alignment", kind: "choice", choices: [["left", "Left"], ["center", "Centre"], ["right", "Right"]], default: "left" },
  ];
  const v = await formDialog({ title: "Text", params, width: 440, ok: "OK", remember: "text", description: FONT_NOTE,
    extra: fontFilePicker(own), validate: x => (String(x.text || "").trim() ? "" : "Write some text first.") });
  if (!v) return null;
  if (own.file) v.font_b64 = own.file.b64;
  return v;
}

/** Text on Curve (the same fields as the desktop's): the values of the op `text_on_curve` or null */
export async function textOnCurveDialog() {
  const fonts = await fontChoices();
  const own = { file: null };
  const params = [
    { name: "text", label: "Text", kind: "str", default: "iyiCAM" },
    { name: "font", label: "Font", kind: "choice", choices: fonts, default: fonts[0][0] },
    { name: "height", label: "Capital height", kind: "length", default: 20, min: 0.1 },
    { name: "spacing", label: "Extra letter spacing", kind: "length", default: 0 },
    { name: "offset", label: "Distance from the vector", kind: "length", default: 0, help: "Positive moves the text to the left of the vector direction" },
    { name: "align", label: "Position", kind: "choice", choices: [["start", "From the start"], ["center", "Centred"], ["end", "To the end"]], default: "center" },
    { name: "reverse", label: "Run along the reversed vector", kind: "bool", default: false },
    { name: "bold", label: "Bold", kind: "bool", default: false },
    { name: "italic", label: "Italic", kind: "bool", default: false },
  ];
  const v = await formDialog({ title: "Text on Curve", params, width: 440, ok: "Create", remember: "text_on_curve", extra: fontFilePicker(own),
    validate: x => (String(x.text || "").trim() ? "" : "Write some text first.") });
  if (!v) return null;
  if (own.file) v.font_b64 = own.file.b64;
  return v;
}

/** Trace Bitmap's form (the desktop's): the `options` of the op `trace_bitmap` or null.  `sheetWidth`: the result is 80 % of it by default */
export async function traceDialog(name, w, hh, sheetWidth) {
  const params = [
    { name: "width", label: "Width of the result", kind: "length", default: Math.min(sheetWidth * 0.8, 200), min: 0.01 },
    { name: "threshold", label: "Threshold (0-255)", kind: "int", default: 128, min: 1, max: 254, help: "Pixels darker than this become the shape" },
    { name: "mode", label: "Result", kind: "choice", default: "outline", choices: [["outline", "Outlines of the dark areas"], ["centerline", "Centre lines (single lines along strokes)"]] },
    { name: "invert", label: "Trace light areas instead", kind: "bool", default: false },
    { name: "smoothing", label: "Smoothing (pixels)", kind: "float", default: 0.6, min: 0.05, max: 10, decimals: 2 },
    { name: "min_area", label: "Ignore specks smaller than (pixels)", kind: "float", default: 8, min: 0, decimals: 1 },
    { name: "center", label: "Centre on the sheet", kind: "bool", default: true },
  ];
  // (the width follows the sheet, not the last trace)
  return formDialog({ title: `Trace ${name} (${w} x ${hh} px)`, params, width: 440, ok: "Trace", remember: "trace", values: { width: Math.min(sheetWidth * 0.8, 200) } });
}
