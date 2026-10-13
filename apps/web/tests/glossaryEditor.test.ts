import assert from "node:assert/strict";
import test from "node:test";
import {
  existingGlossaryDraft, glossaryPayload, newGlossaryDraft,
} from "../src/glossaryEditor.ts";
import type { GlossaryRecord } from "../src/types.ts";

const existing: GlossaryRecord = {
  id: "fixed-id", created_at: "2026-01-01", updated_at: "2026-01-01",
  term: "칭의", aliases: ["칭이"],
  translations: { en: "justification", ja: "義認", fr: "justification" },
  category: "theology", presets: ["church", "lecture"], boost: 2,
  enabled: true,
};

test("editing multilingual glossary preserves other languages and scopes", () => {
  const draft = existingGlossaryDraft(existing);
  draft.ja = "義認（ぎにん）";
  draft.en = "justification";
  const saved = glossaryPayload(draft, existing);
  assert.equal(saved.translations.en, "justification");
  assert.equal(saved.translations.ja, "義認（ぎにん）");
  assert.equal(saved.translations.fr, "justification");
  assert.deepEqual(saved.presets, ["church", "lecture"]);
  assert.equal(saved.boost, 2);
  assert.deepEqual(saved.aliases, ["칭이"]);
});

test("empty target removes just that translation", () => {
  const draft = existingGlossaryDraft(existing);
  draft.ja = " ";
  const result = glossaryPayload(draft, existing);
  assert.equal(result.translations.ja, undefined);
  assert.equal(result.translations.fr, "justification");
  assert.equal(result.translations.en, "justification");
});

test("global scope uses an empty preset list", () => {
  const draft = newGlossaryDraft("lecture");
  draft.term = "발표자";
  draft.en = "presenter";
  draft.ja = "発表者";
  draft.globalScope = true;
  assert.deepEqual(glossaryPayload(draft).presets, []);
  assert.deepEqual(glossaryPayload(draft).translations, {
    en: "presenter", ja: "発表者",
  });
});

test("prevents silent empty per-preset applicability", () => {
  const draft = newGlossaryDraft("church");
  draft.term = "성화";
  draft.presets = [];
  assert.throws(() => glossaryPayload(draft), /Choose at least one preset/);
});
