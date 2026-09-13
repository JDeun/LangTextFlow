import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";

const srcDir = new URL("../src/", import.meta.url);
const cjk = /[가-힣ぁ-んァ-ン一-龯]/u;
const excluded = new Set(["i18n.tsx"]);
const findings = [];

for (const name of (await readdir(srcDir)).sort()) {
  if (!name.endsWith(".tsx") || excluded.has(name)) continue;
  const content = await readFile(new URL(name, srcDir), "utf8");
  for (const [index, line] of content.split("\n").entries()) {
    if (cjk.test(line)) findings.push(`${join("src", name)}:${index + 1}:${line.trim()}`);
  }
}

if (findings.length) {
  console.error("Unlocalized CJK UI literals found outside the locale catalog:\n");
  console.error(findings.join("\n"));
  process.exit(1);
}

console.log("UI i18n literal gate passed.");
