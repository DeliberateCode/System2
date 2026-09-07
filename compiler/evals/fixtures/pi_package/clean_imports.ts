// Positive control for the import scanner: only the Pi type package, Node builtins,
// and relative imports. The scanner must report ZERO external specifiers for this file.
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { helper } from "./local-helper";

// import("comment-decoy") and require("comment-decoy-too") are not code.
const documentation = 'Example only: import("string-decoy") and require("other-decoy")';

export default function (pi: ExtensionAPI) {
  return { fs, path, fileURLToPath, helper, documentation };
}
