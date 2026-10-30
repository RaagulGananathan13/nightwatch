// Ships the latest evaluation results with the dashboard: ../eval/results.json is copied to
// public/eval-results.json, which the Eval page reads. It is measured data, not code, so the
// dashboard redeploys whenever `make eval` publishes new results.
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = join(here, "..", "..", "eval", "results.json");
const target = join(here, "..", "public", "eval-results.json");

mkdirSync(dirname(target), { recursive: true });
if (existsSync(source)) {
  copyFileSync(source, target);
  console.log("copied eval/results.json into the dashboard");
} else {
  console.log("no eval/results.json yet; the Eval page will say so");
}
