// Fixture covering prohibited static, side-effect, dynamic, and require imports.
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import * as fs from "node:fs";
import {
  default as _,
} from "lodash";
export {
  z,
} from "zod";
import "@scope/side-effect-pkg";

export default function (pi: ExtensionAPI) {
  const chalk = import("chalk");
  const cp = require("execa");
  return { fs, _, z, chalk, cp };
}
