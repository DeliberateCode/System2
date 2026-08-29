// Positive control for the F12 import scanner: only the pi type package, node: builtins,
// and relative imports. The scanner must report ZERO external specifiers for this file.
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { helper } from "./local-helper";

export default function (pi: ExtensionAPI) {
  return { fs, path, fileURLToPath, helper };
}
