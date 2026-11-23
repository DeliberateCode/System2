# Vendored Pi subagent example

`index.ts` and `agents.ts` are unchanged from [Pi v1.6.0](https://github.com/earendil-works/pi/tree/faac585884ae2c013a6b1400f3e9e77f10b34bac/packages/coding-agent/examples/extensions/subagent). See `LICENSE` for the upstream MIT notice.

The adjacent System2 extension loads the example only if Pi has no configured `subagent` tool. To update the pin, replace both files together and run the Pi loader and subprocess smoke test. No upstream code is fetched at runtime.
