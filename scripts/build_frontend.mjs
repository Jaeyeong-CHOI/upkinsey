import { build } from "esbuild";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

/** Produce self-contained, production browser assets; never starts a dev server. */
export async function buildFrontend({ outdir = path.join(repositoryRoot, "frontend-dist"), logLevel = "info" } = {}) {
  outdir = path.resolve(outdir);
  const result = await build({
    absWorkingDir: repositoryRoot,
    entryPoints: { app: "prototype/main.jsx", landing: "prototype/landing.js" },
    outdir,
    entryNames: "assets/[name]-[hash]",
    assetNames: "assets/[name]-[hash]",
    bundle: true,
    minify: true,
    format: "esm",
    platform: "browser",
    target: ["es2020"],
    jsx: "automatic",
    define: { "process.env.NODE_ENV": '"production"' },
    legalComments: "linked",
    metafile: true,
    write: false,
    logLevel,
  });

  const pages = [
    ["index.html", "prototype/landing.js"],
    ["Resonance.html", "prototype/main.jsx"],
  ];
  const pageOutputs = [];
  for (const [name, entryPoint] of pages) {
    const entry = Object.entries(result.metafile.outputs).find(([, metadata]) => metadata.entryPoint === entryPoint && metadata.cssBundle);
    if (!entry) throw new Error(`Missing JS/CSS bundle for ${entryPoint}`);
    const [script, metadata] = entry;
    const relativeAsset = filename => path.relative(outdir, path.resolve(repositoryRoot, filename)).split(path.sep).join("/");
    const template = await readFile(path.join(repositoryRoot, "prototype", name), "utf8");
    if (!template.includes("<!-- UPKINSEY_STYLES -->") || !template.includes("<!-- UPKINSEY_SCRIPT -->")) {
      throw new Error(`Missing build placeholders in ${name}`);
    }
    const html = template
      .replace("<!-- UPKINSEY_STYLES -->", `<link rel="stylesheet" href="${relativeAsset(metadata.cssBundle)}" />`)
      .replace("<!-- UPKINSEY_SCRIPT -->", `<script type="module" src="${relativeAsset(script)}"></script>`);
    pageOutputs.push({ path: path.join(outdir, name), contents: html });
  }

  // Finish compilation before touching the active output. Hashed files are
  // written before HTML, so a failed build keeps the previous entry pages usable.
  for (const file of [...result.outputFiles, ...pageOutputs]) {
    await mkdir(path.dirname(file.path), { recursive: true });
    await writeFile(file.path, file.contents);
  }
  return { outdir, metafile: result.metafile };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await buildFrontend();
}
