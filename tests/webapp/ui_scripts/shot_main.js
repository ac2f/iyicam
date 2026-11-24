const { open, shot, loadFixture } = require("./lib");
(async () => {
  const { browser, page, errors } = await open();
  await loadFixture(page);
  await page.waitForTimeout(1500);
  await shot(page, "main");
  console.log(errors.join("\n") || "no errors");
  await browser.close();
})();
