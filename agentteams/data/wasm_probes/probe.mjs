// probe.mjs — the JavaScript guest deny probe (WASM sandbox plan P04). Runs INSIDE the QuickJS-ng WASI guest only.
//
// UNVERIFIED: no QuickJS-ng build was runnable when this was written (release downloads blocked; P04 step 7 is the
// merge gate). The module names ('qjs:std' / 'qjs:os', falling back to 'std' / 'os'), top-level await and the
// std/os call shapes follow the QuickJS-ng documentation and are re-confirmed by step 7 before anything relies on them.
//
// agentteams.wasm_guests.probe_language("javascript", ...) passes this file as /src/main.mjs and parses only its
// "PROBE <name> DENIED|ALLOWED|ABSENT [detail]" lines (C-4: the output is data). A read that succeeds prints what it
// read, so a canary the host planted shows up in the output. Modes (scriptArgs[1]): deny (default), spin, grow.

const load = async (a, b) => { try { return await import(a); } catch (e) { try { return await import(b); } catch (e2) { return null; } } };
const std = await load("qjs:std", "std");
const os = await load("qjs:os", "os");
const CANARY_ENV = "AGENTTEAMS_WASM_CANARY";

function emit(name, verdict, detail) {
  const text = String(detail ?? "").split(/\s+/).join(" ").slice(0, 200);
  console.log(`PROBE ${name} ${verdict} ${text}`.trimEnd());
}

// action returns what it obtained; null/undefined or a throw means DENIED; a missing API means ABSENT.
function attempt(name, need, action) {
  if (!need) { emit(name, "ABSENT", "api missing"); return; }
  try {
    const got = action();
    if (got === null || got === undefined || got === false) emit(name, "DENIED", "refused");
    else emit(name, "ALLOWED", JSON.stringify(got));
  } catch (e) {
    emit(name, "DENIED", e && e.name ? e.name : String(e));
  }
}

const read = (path) => (std.loadFile ? std.loadFile(path) : null);
function write(path, mode) {
  const err = {};
  const fh = std.open(path, mode, err);
  if (!fh) return null;
  fh.puts("probe\n");
  fh.close();
  return path;
}
function readdir(path) {
  const [names, err] = os.readdir(path);
  return err ? null : names;
}

function deny() {
  attempt("fs-abs-file", std && std.loadFile, () => read("/etc/passwd"));
  attempt("fs-abs-list", os && os.readdir, () => readdir("/"));
  attempt("fs-rel", std && std.loadFile, () => read("c/probe-canary.txt"));
  attempt("fs-parent", std && std.loadFile, () => read("../parent-canary.txt"));
  attempt("fs-project", std && std.loadFile, () => read("/project/c/probe-canary.txt"));
  attempt("fs-write-runtime", std && std.open, () => null);  // QuickJS-ng has no runtime mounts
  attempt("fs-write-src", std && std.open, () => write("/src/main.mjs", "a"));
  attempt("fs-write-cwd", std && std.open, () => write("probe-out.txt", "w"));
  attempt("scratch-write", std && std.open, () => write("/scratch/probe-x", "w") && read("/scratch/probe-x"));
  attempt("env", std && std.getenviron, () => { const e = std.getenviron(); return Object.keys(e).length ? e : null; });
  attempt("env-canary", std && std.getenv, () => std.getenv(CANARY_ENV));
  attempt("net-connect", false, null);  // QuickJS-ng has no socket API
  attempt("net-dns", false, null);
  attempt("proc-system", os && os.exec, () => os.exec(["true"]) === 0);
  attempt("proc-spawn", os && os.exec, () => os.exec(["/bin/true"]) === 0);
  attempt("thread", os && os.Worker, () => typeof os.Worker);
  attempt("clock", true, () => Date.now());
  attempt("random", true, () => Math.random());
  if (std && std.err) std.err.puts("PROBE-STDERR\n");
  emit("stdio", "ALLOWED", "stdout+stderr");
}

const args = typeof scriptArgs !== "undefined" ? scriptArgs : [];
const mode = args.length > 1 ? args[1] : "deny";
if (mode === "spin") { for (;;) {} }
else if (mode === "grow") { const blob = new ArrayBuffer(200 << 20); if (!blob.byteLength) std.exit(1); }
else if (mode === "deny") deny();
else { console.log(`unknown mode ${mode}`); if (std) std.exit(2); }
