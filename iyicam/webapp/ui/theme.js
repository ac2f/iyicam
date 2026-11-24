/* iyiCAM web: light and dark as the desktop's theme (Follow the System / Light / Dark). */
import { settings, on, emit } from "./state.js";

const mq = matchMedia("(prefers-color-scheme: dark)");
export function applyTheme() {
  const want = settings.appearance;
  const dark = want === "dark" || (want !== "light" && mq.matches);
  const root = document.documentElement;
  root.dataset.theme = want === "system" ? "" : want;
  root.dataset.resolved = dark ? "dark" : "light";
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = dark ? "#262b33" : "#ffffff";
  emit("theme");
}
export function startTheme() {
  applyTheme();
  mq.addEventListener("change", applyTheme);
  on("settings", applyTheme);
}
