const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { mkdtemp, readFile, access, writeFile } = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');

const repositoryRoot = path.resolve(__dirname, '..');
let directory;
let built;

before(async () => {
  directory = await mkdtemp(path.join(os.tmpdir(), 'upkinsey-frontend-test-'));
  const { buildFrontend } = await import('../scripts/build_frontend.mjs');
  built = await buildFrontend({ outdir: path.join(directory, 'site'), logLevel: 'silent' });
});

test('both production pages reference existing, fingerprinted local JS and CSS', async () => {
  for (const page of ['index.html', 'Resonance.html']) {
    const html = await readFile(path.join(built.outdir, page), 'utf8');
    const scripts = [...html.matchAll(/<script\b[^>]*src="([^"]+)"/g)];
    assert.equal(scripts.length, 1);
    assert.match(html, /<script type="module"/);
    assert.match(scripts[0][1], /^assets\/(app|landing)-[A-Z0-9]+\.js$/);
    const css = html.match(/<link rel="stylesheet" href="([^"]+)"/)[1];
    assert.match(css, /^assets\/(app|landing)-[A-Z0-9]+\.css$/);
    await access(path.join(built.outdir, scripts[0][1]));
    await access(path.join(built.outdir, css));
    assert.doesNotMatch(html, /text\/babel|unpkg\.com|cdn\.jsdelivr\.net|UPKINSEY_(?:STYLES|SCRIPT)/);
  }
  const app = await readFile(path.join(built.outdir, 'Resonance.html'), 'utf8');
  assert.match(app, /<noscript>[\s\S]*JavaScript/);
});

test('the bundle contains production React, no external imports or editor runtime', () => {
  const inputs = Object.keys(built.metafile.inputs);
  assert.ok(inputs.some(name => name.endsWith('react.production.min.js')));
  assert.ok(inputs.some(name => name.endsWith('react-dom.production.min.js')));
  assert.ok(!inputs.some(name => /babel|tweaks-panel|prototype\/app\.js$|\.development\.js$/.test(name)));
  for (const output of Object.values(built.metafile.outputs)) {
    assert.ok(output.imports.every(item => !item.external), 'a browser dependency escaped the bundle');
  }
});

test('all six research screens render through the explicit module dependency graph', async () => {
  const { build } = await import('esbuild');
  const result = await build({
    absWorkingDir: repositoryRoot,
    stdin: {
      resolveDir: path.join(repositoryRoot, 'prototype'),
      sourcefile: 'screen-regression.jsx',
      loader: 'jsx',
      contents: `
        import React from 'react';
        import { renderToStaticMarkup } from 'react-dom/server';
        import { EXAMPLE_BRIEF } from './data.js';
        import { mapResultToResonance } from './research-ui.js';
        import { BriefScreen, RunScreen } from './screens-brief-run.jsx';
        import { SignalsScreen } from './screens-signals.jsx';
        import { PersonasScreen } from './screens-personas.jsx';
        import { AnalystScreen } from './screens-analyst.jsx';
        import { ReportScreen } from './screens-report.jsx';
        const result = { adoption_score: 65, need_fit_score: 50, persona_reactions: [{ name: 'Fixture', adoption_likelihood: 65 }], reaction_distribution: { positive: 100, neutral: 0, negative: 0 } };
        const data = mapResultToResonance(result, EXAMPLE_BRIEF);
        export const screens = [
          <BriefScreen brief={EXAMPLE_BRIEF} />,
          <RunScreen brief={EXAMPLE_BRIEF} />,
          <SignalsScreen data={data.signals} versions={data.versions} result={result} />,
          <PersonasScreen personas={data.personas} mode="cards" />,
          <AnalystScreen result={result} />,
          <ReportScreen result={result} data={data} />,
        ].map(screen => renderToStaticMarkup(screen));
        export const constellation = renderToStaticMarkup(<PersonasScreen personas={data.personas} mode="constellation" />);
      `,
    },
    bundle: true,
    platform: 'node',
    format: 'cjs',
    define: { 'process.env.NODE_ENV': '"production"' },
    write: false,
    logLevel: 'silent',
  });
  const fixture = path.join(directory, 'render-screens.cjs');
  await writeFile(fixture, result.outputFiles[0].contents);
  const { screens, constellation } = require(fixture);
  assert.equal(screens.length, 6);
  for (const [index, name] of ['Brief', 'Run', 'Signals', 'Personas', 'Analyst', 'Report'].entries()) {
    assert.match(screens[index], new RegExp(`data-screen-label="0${index + 1} ${name}"`));
  }
  assert.match(screens[3], /Fixture/);
  for (const [markup, selector] of [[screens[3], 'pcard'], [constellation, 'persona-node']]) {
    assert.match(markup, new RegExp('<button[^>]+type="button"[^>]+aria-pressed="true"[^>]+aria-label="Fixture 응답자 선택"[^>]+class="' + selector + ' selected"'));
    assert.match(markup, /<textarea[^>]+aria-label="Fixture님에게 후속 질문"/);
  }
  assert.match(screens[2], /100%/);
});
