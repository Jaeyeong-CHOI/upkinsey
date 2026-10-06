# Research validity and interpreting outputs

Upkinsey는 실제 고객 조사를 시작하기 전에 가설과 질문을 좁히는 도구입니다.
합성 응답 수는 실제 조사 참여자 수가 아니며, 점수는 구매 확률이 아닙니다.

## What the results mean

- **Persona reactions are generated, not observed.** They are model responses
  conditioned on a product brief and persona context, not interviews with real
  people.
- **Adoption, need-fit and understanding are 0–100 model-generated scores.** A
  score of 70 does not mean a 70% probability of purchase or 70% market demand.
- **Reaction distribution is a percentage of the simulated panel.** The current
  aggregation classifies adoption scores of at least 65 as positive, below 40 as
  negative, and the remainder as neutral. Rounded shares can total 99% or 101%.
  These cutoffs are UI/aggregation conventions, not empirically calibrated labels.
- **Evidence quality is a heuristic check, not statistical confidence.** It can
  flag incomplete inputs, limited panels or other weaknesses. It is not a
  confidence interval, validated accuracy score or certification.
- **Persona selection does not establish representativeness.** More synthetic
  responses, a demographic filter, or a dataset's breadth do not establish that
  the selected panel represents a target population. Responses from one model
  may share biases and should not be treated as independent survey observations.

## Interpretation workflow

1. State a falsifiable product hypothesis and the audience it concerns. Include
   pricing, alternatives and context so the model need not guess missing inputs.
2. Inspect individual reactions and objections, not just aggregate scores. Check
   the persona context, failed calls and warnings. Missing fields stay missing;
   the interface must not substitute sample personas, scores or answers.
3. Treat an apparently strong pattern as a candidate interview question or
   experiment. Check for leading prompts and demographic stereotypes.
4. Test the hypothesis with real customers using a suitable recruitment and
   measurement plan. Record disagreements as well as agreement. Do not convert
   a synthetic finding into a claim about observed customer demand.
5. Revisit the model/prompt only with a documented evaluation objective. Avoid
   repeatedly tuning until the output confirms a preferred business conclusion.

## Two separate kinds of verification

**Software regression checks** verify parsing, scoring conventions, input
validation, request handling and preservation of actual results. Tests with
mocked responses are useful for this, and should work without API keys.

**Empirical research evaluation** compares clearly specified predictions or
patterns with appropriate human evidence. Passing unit tests, producing fluent
persona dialogue or matching one anecdote does not establish this validity.
Upkinsey does not currently claim population-level predictive validation.

For a future evaluation, predefine the task and success criteria, document the
human sample and consent/data rights, hold out evaluation evidence from prompt
tuning, examine subgroup errors and compare against simple baselines. Report
negative findings and limits; do not rely solely on another LLM's plausibility
rating. This is a proposed evaluation approach, not an implemented benchmark.

## Reproducibility and changes

New saved runs include schema version 1 and provenance: application version,
actual model identifier when available, requested seed and separately observed
panel sampling seeds (unknown if metadata is missing), source/selected counts,
selected-panel and prompt hashes, known dataset sources/revisions, start time and
duration. Missing identifiers or revisions remain unknown rather than inferred.
Older runs may not contain this metadata.

New persona samples have a versioned SHA-256 manifest, and cache reuse checks
exact row counts and matching sampling metadata. `UPKINSEY_PERSONA_REVISION`
(or the sampling CLI’s `--revision`) passes a requested commit/ref to the dataset
loader and records it. A branch name remains a requested reference, not a
resolved immutable commit. Prefer a verified commit when tracing a study;
a checksum verifies local artifact integrity, not data representativeness.

When comparing runs, also retain the original permitted brief/panel or sampling
manifest, model/provider settings, code commit and failures. Hashes identify a
change but cannot reconstruct the original input; source revision capture is
still incomplete when the supplied persona records omit it. A fixed sampling
seed preserves a sampling choice, not a guarantee of identical upstream model
responses. Logical request counts are not provider-retry counts or billing totals.

The local SQLite graph projects relationships and per-run evidence from saved
JSON. Graph observation counts are not unique human respondents or independent
empirical evidence. Graph retrieval is not used to ground model prompts. See
[graph storage and interpretation](knowledge-graph.md).

Separate example/replay data from newly generated results. Illustrative avatars
and constellation positions are visual aids, not demographic evidence or a
measured similarity embedding. No historical trend should be displayed without
actual comparison data.

## Related primary references

- [TinyTroupe's Responsible AI FAQ](https://github.com/microsoft/TinyTroupe/blob/main/RESPONSIBLE_AI_FAQ.md)
  distinguishes simulation from demonstrated human-behavior validity.
- [TinyTroupe examples](https://github.com/microsoft/TinyTroupe#examples) show
  comparisons with real survey results, rather than only plausible conversations.
- [EDSL](https://github.com/expectedparrot/edsl) illustrates structured experiments
  and reproducible result handling.

These references motivate the workflow; they do not validate Upkinsey's outputs.
