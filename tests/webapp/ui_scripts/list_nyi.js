/* Lists the menu items and tool buttons the web app does not do yet (greyed "Not on the web yet"). */
const { open } = require("./lib.js");
(async () => {
  const { browser, page } = await open();
  const out = await page.evaluate(async () => {
    const { MENUS } = await import("./ui/menudef.js");
    const { A } = window.__iyicam;
    const res = [];
    const walk = (items, path) => { for (const it of items) { if (it === "-") continue; if (it.menu) walk(it.menu, path + it.text + " > "); else if (!A.registry.has(it.id)) res.push(it.id + "\t" + path + it.text); } };
    walk(MENUS, "");
    return res;
  });
  console.log(out.join("\n"));
  await browser.close();
})();
