const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { getEventListeners } = require('node:events');
let ui;
before(async () => { ui = await import('../prototype/research-ui.js'); });

test('zero is a valid reproducible sampling seed', () => {
  assert.equal(ui.toBackendBrief({}, { seed: 0 }).seed, 0);
  assert.equal(ui.toBackendBrief({}).seed, 42);
});

test('missing demographics and scores are not invented', () => {
  const persona = ui.mapPersona({ name: 'Unknown' });
  assert.equal(persona.age, null);
  assert.equal(persona.region, '');
  assert.equal(persona.role, '');
  assert.equal(persona.adoption, null);
  assert.equal(persona.price, '미제공');
  assert.equal(persona.stance, 'unknown');
  assert.equal(ui.personaBio(persona), '인구통계 정보 미제공');
  assert.equal(ui.scoreLabel(persona.adoption), '미제공');
  assert.equal(ui.parseMeta('개발자').region, '');
  assert.equal(ui.parseMeta('20대 · 서울 · 개발자').age, null);
});

test('source demographics take precedence, canonical legacy metadata still works', () => {
  const persona = ui.mapPersona({
    meta: '58세 · 서울 · 개발자',
    persona_context: { age: 41, province: '제주', occupation: '연구원' },
  });
  assert.equal(ui.personaBio(persona), '41세 · 제주 · 연구원');
  assert.deepEqual(ui.parseMeta('58세 · 서울 · 개발자'), { age: 58, region: '서울', role: '개발자' });
});

test('persona stance matches backend aggregation boundaries, preserving zero scores', () => {
  const values = [0, 39, 40, 64, 65, 100];
  assert.deepEqual(values.map(adoption_likelihood => ui.mapPersona({ adoption_likelihood }).stance), ['neg', 'neg', 'neu', 'neu', 'pos', 'pos']);
  assert.equal(ui.mapPersona({ adoption_likelihood: 0 }).adoption, 0);
});

test('empty real results never fall back to demonstration personas or confidence', () => {
  const result = ui.mapResultToResonance({ persona_reactions: [], personas: [{ name: 'Legacy' }] }, {});
  assert.deepEqual(result.personas, []);
  assert.equal(result.signals.calls.done, 0);
  assert.equal(result.signals.evidenceQuality.value, '미제공');
  assert.equal(result.signals.adoption.value, null);
});

test('percentages remain separate from respondent counts and requested panel size', () => {
  const result = ui.mapResultToResonance({
    persona_reactions: [39, 40, 65].map(adoption_likelihood => ({ adoption_likelihood })),
    reaction_distribution: { positive: 33, neutral: 33, negative: 33 },
    adoption_score: 48,
    request_budget: { requested_sample_size: 5, failed_persona_calls: 2 },
  }, {});
  assert.deepEqual(result.signals.distribution, { positive: 33, neutral: 33, negative: 33 });
  assert.deepEqual(result.signals.distributionCounts, { positive: 1, neutral: 1, negative: 1 });
  assert.equal(result.signals.calls.done, 3);
  assert.equal(result.signals.calls.total, 5);
  assert.equal(result.signals.adoption.prev, null);
  assert.equal(result.signals.adoption.delta, null);
  assert.equal(result.versions[0].time, '저장 시각 미제공');
});

test('API validation messages survive non-2xx responses', async () => {
  await assert.rejects(ui.readApiResponse(Response.json({ error: 'invalid_input', message: 'sample_size must be <= 100' }, { status: 400 })), /sample_size must be <= 100.*HTTP 400/);
});

test('API busy/rate-limit failures explain the cause and retry interval', async () => {
  await assert.rejects(ui.readApiResponse(Response.json({ error: 'too_many_active_jobs' }, { status: 429 })), /다른 시뮬레이션/);
  await assert.rejects(ui.readApiResponse(Response.json({ error: 'rate_limited', retry_after_seconds: 15 }, { status: 429 })), /15초 후 재시도/);
});

test('HTML upstream error bodies do not leak into the UI', async () => {
  await assert.rejects(ui.readApiResponse(new Response('<html>private proxy details</html>', { status: 503 })), error => error.message.includes('HTTP 503') && !error.message.includes('private'));
  await assert.rejects(ui.readApiResponse(new Response('not json', { status: 200 })), /응답 형식/);
  assert.deepEqual(await ui.readApiResponse(Response.json({ ok: true })), { ok: true });
});

test('poll delay removes abort listeners after completion', async () => {
  const controller = new AbortController();
  for (let i = 0; i < 20; i++) await ui.sleep(0, controller.signal);
  assert.equal(getEventListeners(controller.signal, 'abort').length, 0);
});

test('poll delay respects both pending and already-aborted requests', async () => {
  const controller = new AbortController();
  const pending = ui.sleep(60_000, controller.signal);
  controller.abort();
  await assert.rejects(pending, { name: 'AbortError' });
  assert.equal(getEventListeners(controller.signal, 'abort').length, 0);
  await assert.rejects(ui.sleep(60_000, controller.signal), { name: 'AbortError' });
});


test('chat Enter submits only after IME composition ends and preserves Shift+Enter', () => {
  assert.equal(ui.shouldSubmitChatOnEnter({ key: 'Enter' }), true);
  assert.equal(ui.shouldSubmitChatOnEnter({ key: 'Enter', shiftKey: true }), false);
  assert.equal(ui.shouldSubmitChatOnEnter({ key: 'Escape' }), false);
  assert.equal(ui.shouldSubmitChatOnEnter({ key: 'Enter', isComposing: true }), false);
  assert.equal(ui.shouldSubmitChatOnEnter({ key: 'Enter', nativeEvent: { isComposing: true } }), false);
  assert.equal(ui.shouldSubmitChatOnEnter({ key: 'Enter', keyCode: 229 }), false);
  assert.equal(ui.shouldSubmitChatOnEnter({ key: 'Enter', nativeEvent: { isComposing: false, keyCode: 229 } }), false);
  assert.equal(ui.shouldSubmitChatOnEnter({ key: 'Enter', nativeEvent: { isComposing: false, keyCode: 13 } }), true);
});
