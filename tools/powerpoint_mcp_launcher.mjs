import { existsSync, readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { spawn } from "node:child_process";

function candidates() {
  const explicit = process.env.AUTOFIGURE_POWERPOINT_SERVER;
  if (explicit) return [resolve(explicit)];
  const packageRoot = join(
    homedir(),
    ".codex",
    "plugins",
    "cache",
    "ai-scientific-illustration-tools",
    "drawio-scientific-illustrator",
  );
  if (!existsSync(packageRoot)) return [];
  return readdirSync(packageRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => join(packageRoot, entry.name, "scripts", "powerpoint-server.mjs"))
    .filter((path) => existsSync(path))
    .sort((left, right) => statSync(right).mtimeMs - statSync(left).mtimeMs);
}

const server = candidates()[0];
if (!server) {
  process.stderr.write(
    "powerpoint-live server was not found. Install/enable the scientific illustrator " +
      "plugin, or set AUTOFIGURE_POWERPOINT_SERVER to its powerpoint-server.mjs path.\n",
  );
  process.exit(1);
}
if (process.argv.includes("--print-server")) {
  process.stdout.write(`${server}\n`);
  process.exit(0);
}

const child = spawn(process.execPath, [server], {
  cwd: dirname(dirname(server)),
  env: process.env,
  stdio: "inherit",
});
child.on("error", (error) => {
  process.stderr.write(`failed to start powerpoint-live: ${error.message}\n`);
  process.exit(1);
});
child.on("exit", (code, signal) => {
  process.exitCode = code ?? (signal ? 1 : 0);
});
