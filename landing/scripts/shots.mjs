// Visual check: screenshots every section at desktop and phone widths using the
// locally installed Edge, and reports console errors.
//   npm run build && npm run preview   (in one terminal)
//   npm run shots                      (in another)
import puppeteer from "puppeteer-core";
import { mkdirSync } from "node:fs";

const URL = process.env.SHOT_URL || "http://localhost:5181/";
const OUT = process.env.SHOT_DIR || "shots";
const EDGE = process.env.EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const only = process.argv[2]; // optional: "desktop" | "wide" | "mobile"

const viewports = {
  desktop: { width: 1440, height: 900, deviceScaleFactor: 1 },
  wide: { width: 2040, height: 930, deviceScaleFactor: 1 },
  mobile: { width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true },
};
// name -> how to scroll there. "center:" puts the element's centre mid-viewport.
const targets = [
  ["01-hero", "top:#top"],
  ["02-step1", "center:[data-step='1'] .phone-slot"],
  ["03-step2", "center:[data-step='2'] .phone-slot"],
  ["04-step3", "center:[data-step='3'] .phone-slot"],
  ["05-step4", "center:[data-step='4'] .phone-slot"],
  ["06-step5", "center:[data-step='5'] .phone-slot"],
  ["07-roles", "center:#roles-slot"],
  ["08-platform", "top:#platform"],
  ["09-platform-2", "center:.tile-full"],
  ["10-trust", "top:#trust"],
  ["11-next", "top:#roadmap"],
  ["12-contact", "top:#contact"],
];

mkdirSync(OUT, { recursive: true });
const browser = await puppeteer.launch({
  executablePath: EDGE,
  headless: true,
  userDataDir: ".browser-profile", // keep the browser's temp profile beside the project, not in the system temp folder
  args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--hide-scrollbars"],
});

let problems = 0;
for (const [name, vp] of Object.entries(viewports)) {
  if (only && only !== name) continue;
  const page = await browser.newPage();
  await page.setViewport(vp);
  page.on("console", (m) => {
    if (m.type() === "error") {
      problems++;
      console.log(`[${name}] console error:`, m.text());
    }
  });
  page.on("pageerror", (e) => {
    problems++;
    console.log(`[${name}] page error:`, e.message);
  });
  await page.goto(URL, { waitUntil: "networkidle0" });
  await new Promise((r) => setTimeout(r, 3200));
  console.log(`[${name}] webgl:`, await page.evaluate(() => document.documentElement.className));

  for (const [label, how] of targets) {
    const [mode, sel] = [how.slice(0, how.indexOf(":")), how.slice(how.indexOf(":") + 1)];
    await page.evaluate(
      (mode, sel) => {
        const r = document.querySelector(sel).getBoundingClientRect();
        const y = mode === "center" ? r.top + r.height / 2 - innerHeight / 2 : r.top;
        window.scrollTo({ top: scrollY + y, behavior: "instant" });
      },
      mode,
      sel,
    );
    await new Promise((r) => setTimeout(r, 2600));
    await page.screenshot({ path: `${OUT}/${name}-${label}.png` });
  }
  // Measured against the requested width: on phones an overflowing element widens
  // the layout viewport itself, so comparing with innerWidth would hide it.
  const overflow = await page.evaluate(
    (w) => Math.round(Math.max(document.documentElement.scrollWidth, innerWidth, document.querySelector("#nav").getBoundingClientRect().right) - w),
    vp.width,
  );
  if (overflow > 0) {
    problems++;
    console.log(`[${name}] horizontal overflow: ${overflow}px`);
  }
  await page.close();
}
await browser.close();
console.log(problems ? `${problems} problem(s) found` : "no console errors, no horizontal overflow");
