// Hostile fixture: a generated-looking extension that pulls in EXTERNAL npm modules.
// The F12 import scanner must flag every bare specifier here that is not the pi type
// package or a node: builtin. Exercises the static, side-effect, dynamic, and require
// import forms so the scanner cannot miss any surface.
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import * as fs from "node:fs";
import _ from "lodash";
import { z } from "zod";
import "@scope/side-effect-pkg";

export default function (pi: ExtensionAPI) {
  const chalk = import("chalk");
  const cp = require("execa");
  return { fs, _, z, chalk, cp };
}
