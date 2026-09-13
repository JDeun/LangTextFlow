import { readdir, readFile } from "node:fs/promises";

const src = new URL("../src/", import.meta.url);
const findings = [];

async function scan(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const url = new URL(entry.name + (entry.isDirectory() ? "/" : ""), directory);
    if (entry.isDirectory()) {
      await scan(url);
      continue;
    }
    if (!entry.name.endsWith(".css")) continue;
    const text = await readFile(url, "utf8");
    const name = decodeURIComponent(url.pathname.split("/apps/web/").at(-1));
    if (text.includes("\\n") || text.includes("\\r")) {
      findings.push(`${name}: contains literal escaped newline characters`);
    }
    for (const [label, pattern] of [
      ["CSS expression()", /expression\s*\(/i],
      ["javascript URL", /javascript\s*:/i],
      ["HTML data URL", /data\s*:\s*text\/html/i],
    ]) {
      if (pattern.test(text)) findings.push(`${name}: contains ${label}`);
    }
    const stripped = text
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/g, "");
    let balance = 0;
    for (const char of stripped) {
      if (char === "{") balance += 1;
      if (char === "}") balance -= 1;
      if (balance < 0) break;
    }
    if (balance !== 0) findings.push(`${name}: unbalanced CSS braces (${balance})`);
  }
}

await scan(src);
if (findings.length) {
  console.error("CSS hygiene gate failed:\n" + findings.join("\n"));
  process.exit(1);
}
console.log("CSS hygiene gate passed.");
